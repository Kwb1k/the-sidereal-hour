"""Portable materials for the Sidereal Hour Blender-to-glTF export."""
import math
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
import zlib

import bpy
import numpy as np


def _tile(size, seed):
    rng = np.random.default_rng(seed)
    coord = np.arange(size, dtype=np.float32) / size

    def field(grid):
        cells = rng.normal(0, 1, (grid, grid))
        at = coord * grid
        lo = np.floor(at).astype(int)
        hi = (lo + 1) % grid
        fraction = at - lo
        w = fraction ** 2 * (3 - 2 * fraction)
        i, j = np.ix_(lo, lo)
        ii, jj = np.ix_(hi, hi)
        top = cells[i, j] * (1 - w) + cells[i, jj] * w
        bottom = cells[ii, j] * (1 - w) + cells[ii, jj] * w
        return top * (1 - w[:, None]) + bottom * w[:, None]

    cloud = sum(weight * field(grid) for grid, weight in
                ((4, .68), (8, .39), (16, .23), (32, .13), (64, .07)))
    cloud = (cloud - cloud.mean()) / cloud.std()
    return cloud


def _png(rgb):
    def chunk(kind, data):
        crc = zlib.crc32(kind + data) & 0xffffffff
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', crc)
    h, w, _ = rgb.shape
    raw = rgb.astype(np.uint8)
    delta = raw.copy()
    delta[:, 1:] -= raw[:, :-1]
    scanlines = b''.join(b'\x01' + row.tobytes() for row in delta)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(scanlines, 9)) + chunk(b'IEND', b''))


def _image(label, pixels, non_color=False):
    # sRGB bytes are written explicitly so the texture remains identical when
    # Blender repacks or recompresses it during GLB export.
    with TemporaryDirectory(prefix="sidereal-texture-") as directory:
        path = Path(directory) / (label + ".png")
        path.write_bytes(_png(pixels))
        image = bpy.data.images.load(str(path))
        if non_color:
            image.colorspace_settings.name = 'Non-Color'
        image.pack()
        image.name = label
    return image


def prepare_materials(objects):
    """Preserve source PBR factors, lunar color data, and bake patterned surfaces."""
    objects = list(objects)
    materials = {slot.material for obj in objects for slot in obj.material_slots
                 if slot.material is not None}
    color_maps = {}
    fine = _tile(128, 131)
    dx = (np.roll(fine, -1, 1) - np.roll(fine, 1, 1)) * .17
    dy = (np.roll(fine, -1, 0) - np.roll(fine, 1, 0)) * .17
    normals = np.stack((-dx, dy, np.ones_like(dx)), axis=2)
    normals /= np.linalg.norm(normals, axis=2, keepdims=True)
    nmap = None
    for mat in sorted(materials, key=lambda value: value.name):
        if not mat.use_nodes:
            continue
        tree = mat.node_tree
        bs = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bs is None:
            continue
        base = bs.inputs['Base Color']
        rough = bs.inputs['Roughness']
        normal = bs.inputs['Normal']
        # The moon already has an accurate per-vertex crater/albedo map.
        if mat.name.startswith("Equatorium | alabaster lunar"):
            emission = bs.inputs['Emission Color']
            for link in list(emission.links):
                tree.links.remove(link)
            emission.default_value = (.60, .55, .45, 1)
            continue
        if mat.name.startswith("Case |"):
            if rough.links:
                ramp = rough.links[0].from_node
                if ramp.type == 'VALTORGB':
                    rough.default_value = sum(e.color[0] for e in ramp.color_ramp.elements) / len(ramp.color_ramp.elements)
            for socket in (rough, normal):
                for link in list(socket.links):
                    tree.links.remove(link)
            continue
        if not mat.name.startswith("Observatory |") or not base.links:
            continue
        ramp = base.links[0].from_node
        if ramp.type != 'VALTORGB':
            continue
        endpoints = ramp.color_ramp.elements
        low = np.array(endpoints[0].color[:3])
        high = np.array(endpoints[-1].color[:3])
        input_noise = ramp.inputs['Fac'].links[0].from_node
        freq = input_noise.inputs['Scale'].default_value
        seed = 307 if 'copper' in mat.name else (308 if 'stone' in mat.name or 'travertine' in mat.name else 309)
        texture = _tile(128, seed)
        t = np.clip((.5 + .15 * texture - endpoints[0].position)
                    / (endpoints[-1].position - endpoints[0].position), 0, 1)
        linear = low + t[:, :, None] * (high - low)
        encoded = np.where(linear <= .0031308, linear * 12.92,
                           1.055 * np.power(linear, 1 / 2.4) - .055)
        label = mat.name.split('|')[-1].strip()
        color = _image(label, np.rint(255 * encoded))
        albedo = tree.nodes.new('ShaderNodeTexImage')
        albedo.image = color
        tree.links.new(albedo.outputs['Color'], base)
        if normal.links:
            if nmap is None:
                nmap = _image("Mineral grain", np.rint((normals * .5 + .5) * 255), True)
            normal_pixels = tree.nodes.new('ShaderNodeTexImage')
            normal_pixels.image = nmap
            normal_map = tree.nodes.new('ShaderNodeNormalMap')
            normal_map.inputs['Strength'].default_value = .47
            tree.links.new(normal_pixels.outputs['Color'], normal_map.inputs['Color'])
            tree.links.new(normal_map.outputs['Normal'], normal)
        # Approximately four coarse noise lobes per image repeat.
        color_maps[mat] = freq / 4.
    for ob in objects:
        if ob.type != 'MESH':
            continue
        mesh = ob.data
        factors = [color_maps.get(s.material) for s in ob.material_slots]
        if not any(factors):
            continue
        uv = mesh.uv_layers.active
        if uv is None:
            uv = mesh.uv_layers.new(name='Weathering')
        vals = np.zeros((len(mesh.loops), 2), np.float32)
        verts = np.empty((len(mesh.vertices), 3), np.float32)
        mesh.vertices.foreach_get('co', verts.ravel())
        indices = np.empty(len(mesh.loops), np.int32)
        mesh.loops.foreach_get('vertex_index', indices)
        for poly in mesh.polygons:
            factor = factors[poly.material_index]
            if factor is None:
                continue
            n = poly.normal
            axis = max(range(3), key=lambda i: abs(n[i]))
            u, v = ((1, 2), (0, 2), (0, 1))[axis]
            loops = slice(poly.loop_start, poly.loop_start + poly.loop_total)
            coords = verts[indices[loops]]
            vals[loops, 0] = factor * coords[:, u]
            vals[loops, 1] = factor * coords[:, v]
        uv.data.foreach_set('uv', vals.ravel())
        mesh.update()
    return len(color_maps)
"""Bake the mechanism's four-second hero cycle onto rest-pose pivot nodes."""
import math

import bpy
from mathutils import Quaternion, Vector


def add_sidereal_motion(pivots):
    """Key local rotations relative to the frame-193 pose and return glTF kwargs."""
    # Quaternion channels express the original rotations in each pivot's own
    # frame. This preserves the inclined lunar axis and the nested carriage.
    z_axis = Vector((0.0, 0.0, 1.0))
    x_axis = Vector((1.0, 0.0, 0.0))
    cycle_frames = 96

    def key_delta(name, axis, radians_at, step):
        node = pivots[name]
        rest = node.matrix_basis.decompose()[1].normalized()
        node.animation_data_clear()
        node.rotation_mode = 'QUATERNION'
        previous = rest
        for frame in range(0, cycle_frames + 1, step):
            turn = Quaternion(axis, radians_at(frame / cycle_frames))
            rotation = rest @ turn
            rotation.make_compatible(previous)
            node.rotation_quaternion = rotation
            previous = rotation
            node.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        action = node.animation_data.action
        action.name = f"Sidereal hour | {name}"
        for curve in action.fcurves:
            for point in curve.keyframe_points:
                point.interpolation = 'LINEAR'
        node.rotation_quaternion = rest

    # A 24-tooth wheel drives each 12-tooth wheel through twice the
    # angle with opposite sign. Intermediate samples disambiguate full turns.
    for number in range(1, 7):
        turns = 1 if number % 2 == 1 else -2
        key_delta(f"Wheel{number:02d}", z_axis,
                  lambda t, turns=turns: math.tau * turns * t, 8)
    key_delta("MoonGlobe", z_axis, lambda t: math.tau * t, 8)
    key_delta("MoonCarriage", x_axis,
              lambda t: 0.21 * math.sin(math.tau * t), 2)
    for name in ("DomeMotor", "TelescopeMotor"):
        key_delta(name, z_axis,
                  lambda t: 0.34 * (1.0 - math.cos(math.tau * t)), 2)

    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 0, cycle_frames
    scene.render.fps, scene.render.fps_base = 24, 1.0
    scene.frame_set(0)
    return dict(
        export_animations=True,
        export_animation_mode='ACTIVE_ACTIONS',
        export_nla_strips_merged_animation_name='Sidereal cycle',
        export_frame_range=True,
        export_force_sampling=False,
        export_optimize_animation_size=False,
    )

def compact_vertex_streams(path):
    """Quantize rigid, unskinned geometry using native glTF vertex attributes.

    Only leaf geometry nodes receive dequantization transforms. Motion pivots
    and top-level exploded-view controls keep their original transforms.
    """
    import json
    import struct

    header=struct.Struct("<I4s")
    original=path.read_bytes()
    json_length,kind=header.unpack_from(original,12)
    assert kind==b"JSON"
    model=json.loads(original[20:20+json_length])
    oldbin=memoryview(original)[28+json_length:]
    views=model["bufferViews"]
    accessors=model["accessors"]
    changed={}
    def read_vec(index,components=3):
        acc=accessors[index]
        v=views[acc["bufferView"]]
        if acc["type"]!=f"VEC{components}" or acc["componentType"]!=5126 or v.get("byteStride",components*4)!=components*4:
            raise ValueError("Expected tightly packed float vectors")
        return np.frombuffer(oldbin,dtype="<f4",count=acc["count"]*components,
                             offset=v.get("byteOffset",0)+acc.get("byteOffset",0)).reshape(-1,components)
    def pack_attr(index, values, dtype, component):
        acc=accessors[index]
        view_id=acc["bufferView"]
        quant=np.asarray(values,dtype=dtype)
        aligned=np.zeros((len(quant),4),dtype=dtype)
        aligned[:,:quant.shape[1]]=quant
        views[view_id]["byteStride"]=aligned.strides[0]
        changed[view_id]=aligned.tobytes()
        acc["componentType"]=component
        acc["normalized"]=True
        if "min" in acc:
            acc["min"]=quant.min(axis=0).astype(int).tolist()
            acc["max"]=quant.max(axis=0).astype(int).tolist()
    for mesh_id,mesh in enumerate(model["meshes"]):
        positions=[p["attributes"]["POSITION"] for p in mesh["primitives"]]
        origin=np.min([accessors[i]["min"] for i in positions],axis=0)
        upper=np.max([accessors[i]["max"] for i in positions],axis=0)
        span=float(np.max(upper-origin)) or 1.0
        for node in model["nodes"]:
            if node.get("mesh")!=mesh_id:
                continue
            if any(key in node for key in ("matrix","translation","rotation","scale","children","skin")):
                raise ValueError("Expected an untransformed geometry leaf")
            node["translation"]=origin.tolist()
            node["scale"]=[span]*3
        for p in mesh["primitives"]:
            pos=p["attributes"]["POSITION"]
            normal=p["attributes"]["NORMAL"]
            coords=np.rint(np.clip((read_vec(pos)-origin)/span,0,1)*65535)
            pack_attr(pos,coords,"<u2",5123)
            pack_attr(normal,np.rint(np.clip(read_vec(normal),-1,1)*127),"i1",5120)
            tangent=p["attributes"].get("TANGENT")
            if tangent is not None:
                pack_attr(tangent,np.rint(np.clip(read_vec(tangent,4),-1,1)*127),"i1",5120)
    # Buffer-view payloads start on four-byte boundaries, including embedded PNGs.
    binary=bytearray()
    for i,v in enumerate(views):
        ofs=v.get("byteOffset",0)
        chunk=changed.get(i,oldbin[ofs:ofs+v["byteLength"]])
        binary.extend(b"\0"*((-len(binary))%4))
        v["byteOffset"]=len(binary)
        v["byteLength"]=len(chunk)
        binary.extend(chunk)
    model["buffers"][0]["byteLength"]=len(binary)
    binary.extend(b"\0"*((-len(binary))%4))
    for key in ("extensionsUsed","extensionsRequired"):
        if "KHR_mesh_quantization" not in model.setdefault(key,[]):
            model[key].append("KHR_mesh_quantization")
    js=json.dumps(model,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    js+=b" "*((-len(js))%4)
    glb=bytearray(struct.pack("<4sII",b"glTF",2,28+len(js)+len(binary)))
    glb.extend(header.pack(len(js),b"JSON"))
    glb.extend(js)
    glb.extend(header.pack(len(binary),b"BIN\0"))
    glb.extend(binary)
    temporary=path.with_suffix(".compact.glb")
    temporary.write_bytes(glb)
    temporary.replace(path)

import sys
from collections import defaultdict
from pathlib import Path

import bpy
from mathutils import Matrix


def simplify_source(obj, collection):
    """Thin small turned profiles while retaining architectural silhouettes."""
    if obj.type == "CURVE" and obj.name == "Double barleycorn chasing":
        original = obj.data
        curve = bpy.data.curves.new("Polished barleycorn fillet", "CURVE")
        curve.dimensions = "3D"
        curve.bevel_depth = original.bevel_depth
        curve.bevel_resolution = 0
        curve.resolution_u = 1
        for mat in original.materials:
            curve.materials.append(mat)
        for spline in original.splines:
            coords = [pt.co.copy() for pt in spline.points][::3]
            line = curve.splines.new("POLY")
            line.points.add(len(coords)-1)
            for pt, co in zip(line.points, coords):
                pt.co=co
            line.use_cyclic_u = True
        obj.data = curve
    elif obj.type in ("CURVE", "FONT"):
        obj.data.bevel_resolution = 0
        obj.data.resolution_u = min(obj.data.resolution_u, 4)
    if obj.type != "MESH":
        return
    for modifier in list(obj.modifiers):
        if modifier.type == 'BEVEL':
            modifier.segments = 1
    ratio = 1.0
    if collection == '01':
        ratio = .28
        if obj.name.startswith(("Two-piece", "120 half", "Fluted onion", "Blued recess")):
            ratio = .55 if not obj.name.startswith("120 half") else 1.0
    elif collection == '03':
        ratio = .40
        if 'involute wheel' in obj.name:
            ratio = .52
    elif collection == '04':
        ratio = .56 if "globe" in obj.name else .50
    elif collection == '02' and obj.name[:2] in ('08','09'):
        ratio = .52
    if ratio < 1 and len(obj.data.polygons) > 220:
        modifier=obj.modifiers.new("Web silhouette", "DECIMATE")
        modifier.ratio=ratio
        modifier.use_collapse_triangulate=True


def sidereal_export(output):
    """Create the web model in memory; preserve the film scene on disk."""
    scene=bpy.context.scene
    scene.frame_set(138)
    codes={}
    for col in bpy.data.collections:
        if col.name[:2] in ("01","02","03","04","06","08"):
            for obj in col.objects:
                codes[obj]=col.name[:2]
    keeper_pose={o:o.matrix_world.copy() for o,code in codes.items()
                 if code=="06" or o.name.startswith(("03L •","03R •"))}
    scene.frame_set(193)
    rest={o:o.matrix_world.copy() for o in codes}
    rest.update(keeper_pose)
    originals={o.name:o for o in codes}
    sources=[o for o in codes if o.type in ('MESH','CURVE','FONT')]
    prepare_materials(sources)
    for o in sources:
        simplify_source(o,codes[o])
        if o.type=="MESH" and codes[o]=="02":
            modifier=o.modifiers.new("Explicit tangent basis","TRIANGULATE")
            modifier.keep_custom_normals=True
    bpy.context.view_layer.update()

    target=bpy.data.collections.new("Web mechanism")
    scene.collection.children.link(target)
    def empty(name,parent=None,world=None):
        o=bpy.data.objects.new(name,None)
        target.objects.link(o)
        if parent:
            o.parent=parent
            o.matrix_parent_inverse=Matrix.Identity(4)
        if world is not None:
            parent_world=parent.matrix_world if parent else Matrix.Identity(4)
            o.matrix_basis=parent_world.inverted_safe() @ world
        bpy.context.view_layer.update()
        return o
    layers={name:empty(name) for name in ("Case","Movement","Armillary","House","Dome","Telescope","Keeper")}
    wheel_sources=sorted((o for o in sources if "involute wheel" in o.name),key=lambda o:o.name)
    if len(wheel_sources)!=6:
        raise ValueError("Expected six meshing wheels in the source watch")
    pivots={}
    wheel_pivots={}
    for i,o in enumerate(wheel_sources,1):
        name=f"Wheel{i:02d}"
        pivots[name]=empty(name,layers["Movement"],rest[o])
        wheel_pivots[o]=pivots[name]
    original_carriage=originals["Lunar | rocking meridian carriage"]
    original_globe=originals["Lunar | carved lunar globe"]
    original_motor=originals["Azimuth motor | revolving shutter and refractor"]
    for name,parent,source in [
        ("MoonCarriage",layers["Armillary"],original_carriage),
        ("MoonGlobe",None,original_globe),
        ("DomeMotor",layers["Dome"],original_motor),
        ("TelescopeMotor",layers["Telescope"],original_motor)]:
        pivots[name]=empty(name,parent or pivots["MoonCarriage"],rest[source])
    def rigid_parent(obj):
        code=codes[obj]
        if code=="01":
            return layers["Movement"] if obj.name.startswith(("Plate turning","Blued recess")) else layers["Case"]
        if code=="08":
            return layers["Movement"]
        if code=="06":
            return layers["Keeper"]
        if code=="02":
            if obj.name[:2] in ('07','08','09'):
                return pivots["DomeMotor"]
            if obj.name.startswith('10 •'):
                return pivots["TelescopeMotor"]
            return layers["House"]
        node=obj
        while node:
            if node in wheel_pivots:
                return wheel_pivots[node]
            if node==original_globe:
                return pivots["MoonGlobe"]
            if node==original_carriage:
                return pivots["MoonCarriage"]
            node=node.parent
        return layers["Movement"] if code=="03" else layers["Armillary"]

    # One object for each rigid mechanism segment. The exporter batches each
    # segment by material, while the top-level empties remain clean lift handles.
    bins=defaultdict(list)
    graph=bpy.context.evaluated_depsgraph_get()
    for source in sources:
        group=rigid_parent(source)
        mesh=bpy.data.meshes.new_from_object(source.evaluated_get(graph),depsgraph=graph,preserve_all_data_layers=True)
        if not mesh or not mesh.polygons:
            continue
        # Only patterned surfaces need UV data. Other source primitive UVs
        # would unnecessarily split vertices at seams in the portable mesh.
        textured=any(slot and slot.use_nodes and any(n.type=='TEX_IMAGE' for n in slot.node_tree.nodes)
                     for slot in mesh.materials)
        if textured and mesh.uv_layers.active:
            surface_uv = mesh.uv_layers.active
            for uv in list(mesh.uv_layers):
                if uv != surface_uv:
                    mesh.uv_layers.remove(uv)
            surface_uv.name = "Weathering"
        if not textured:
            for uv in list(mesh.uv_layers):
                mesh.uv_layers.remove(uv)
        tmp=bpy.data.objects.new(source.name+' | rigid',mesh)
        target.objects.link(tmp)
        tmp.matrix_world=rest[source]
        bins[group].append(tmp)
    for group, children in bins.items():
        combined=bpy.data.objects.new(group.name+" geometry",bpy.data.meshes.new(group.name))
        target.objects.link(combined)
        combined.parent=group
        bpy.context.view_layer.update()
        with bpy.context.temp_override(active_object=combined,object=combined,selected_objects=[combined,*children],
                                       selected_editable_objects=[combined,*children]):
            bpy.ops.object.join()
        combined.name=group.name+" geometry"
    scene.timeline_markers.clear()
    animation=add_sidereal_motion(pivots)
    animation["export_nla_strips_merged_animation_name"]="Sidereal Cycle"
    bpy.ops.object.select_all(action="DESELECT")
    for o in target.objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active=layers["Case"]
    output.parent.mkdir(parents=True,exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(output),export_format="GLB",use_selection=True,
        export_normals=True,export_texcoords=True,export_tangents=True,
        export_materials="EXPORT",export_vertex_color="MATERIAL",
        export_all_vertex_colors=False,export_extras=False,
        export_yup=True,export_cameras=False,export_lights=False,
        export_skins=False,export_morph=False,export_apply=False,
        **animation)
    compact_vertex_streams(output)
    print("Wrote web assembly:", output, output.stat().st_size, "bytes")


if __name__=="__main__":
    if "--" not in sys.argv or not sys.argv[sys.argv.index("--")+1:]:
        raise SystemExit("Usage: blender -b SOURCE.blend -P sidereal_export.py -- OUTPUT.glb")
    sidereal_export(Path(sys.argv[sys.argv.index("--")+1]).resolve())

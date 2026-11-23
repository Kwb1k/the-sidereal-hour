import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const elevation = { Case: 0, Movement: 2.62, Armillary: 2.62, House: 4.49, Dome: 6.96, Telescope: 5.35, Keeper: 4.49 };
const groups = {
  all: Object.keys(elevation),
  case: ['Case'],
  gears: ['Movement'],
  armillary: ['Armillary'],
  observatory: ['House', 'Dome', 'Telescope'],
  keeper: ['Keeper'],
  dome: ['Dome'],
  telescope: ['Telescope'],
};
const defaultView = new THREE.Vector3(.34, .85, 1).normalize();
const partViews = {
  keeper: new THREE.Vector3(.63, .33, 1).normalize(),
  dome: new THREE.Vector3(.40, .64, 1).normalize(),
  telescope: new THREE.Vector3(.93, .62, .70).normalize(),
  observatory: new THREE.Vector3(.48, .4, 1).normalize(),
  armillary: new THREE.Vector3(-.4, .43, 1).normalize(),
  gears: new THREE.Vector3(.22, 1.1, 1).normalize(),
};

// A compact convex outline of each assembly gives a tighter camera fit than
// a box around the watch and its projecting crown. Compute it once at load.
function outline(part) {
  const directions = [];
  for (let latitude = -3; latitude <= 3; latitude++) {
    const pitch = latitude * Math.PI / 6;
    const around = Math.abs(latitude) === 3 ? 1 : 12;
    for (let i = 0; i < around; i++) {
      const angle = Math.PI * 2 * i / around;
      directions.push(new THREE.Vector3(Math.cos(pitch) * Math.cos(angle), Math.sin(pitch), Math.cos(pitch) * Math.sin(angle)));
    }
  }
  const extremes = directions.map(() => new THREE.Vector3());
  const distances = directions.map(() => -Infinity);
  const v = new THREE.Vector3();
  part.traverse(mesh => {
    const vertices = mesh.geometry?.attributes.position;
    if (!vertices) return;
    const stride = Math.max(1, Math.floor(vertices.count / 256));
    for (let i = 0; i < vertices.count; i += stride) {
      v.fromBufferAttribute(vertices, i).applyMatrix4(mesh.matrixWorld);
      directions.forEach((d, j) => {
        const dot = d.dot(v);
        if (dot > distances[j]) {
          extremes[j].copy(v);
          distances[j] = dot;
        }
      });
    }
  });
  return extremes;
}

function lightStudio(scene, renderer) {
  const room = new RoomEnvironment();
  room.traverse(panel => {
    if (panel.material?.emissiveIntensity > 1) {
      panel.material.emissive.set(panel.position.x < -4 ? 0xffe2b4 : panel.position.z < -4 ? 0xa6dfff : 0xfff7e8);
    }
  });
  const pmrem = new THREE.PMREMGenerator(renderer);
  const reflections = pmrem.fromScene(room, .025);
  scene.environment = reflections.texture;
  scene.environmentIntensity = .80;
  scene.environmentRotation.y = -.33;
  room.dispose();
  pmrem.dispose();

  const key = new THREE.DirectionalLight(0xffe0aa, 1.78);
  key.position.set(-5.7, 11, 5.8);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  Object.assign(key.shadow.camera, { left: -7.1, right: 7.1, top: 7.1, bottom: -7.1, near: .2, far: 28 });
  key.shadow.camera.updateProjectionMatrix();
  key.shadow.normalBias = .009;
  key.shadow.bias = -.00028;
  scene.add(key);
  const edge = new THREE.DirectionalLight(0x9bdcfa, 2.0);
  edge.position.set(3, 5, -6);
  scene.add(edge, new THREE.HemisphereLight(0xfff1d8, 0x42616b, .54));

  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 512;
  const context = canvas.getContext('2d');
  const gradient = context.createRadialGradient(250, 218, 15, 250, 245, 365);
  gradient.addColorStop(0, '#294147');
  gradient.addColorStop(.68, '#172b31');
  gradient.addColorStop(1, '#0c1c21');
  context.fillStyle = gradient;
  context.fillRect(0, 0, 512, 512);
  scene.background = new THREE.CanvasTexture(canvas);
  scene.background.colorSpace = THREE.SRGBColorSpace;

  // The transparent receiver leaves the rest of the stage clear and can be
  // viewed through from below when inspecting the underside of the case.
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(36, 36), new THREE.ShadowMaterial({ opacity: .21, depthWrite: false }));
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -.58;
  floor.receiveShadow = true;
  scene.add(floor);
}

async function initialize(root) {
  const stage = root.querySelector('[data-viewer-stage]');
  const status = root.querySelector('[data-viewer-status]');
  const play = root.querySelector('[data-viewer-play]');
  const explode = root.querySelector('[data-viewer-explode]');
  const value = root.querySelector('[data-viewer-explode-value]');
  const focus = root.querySelector('[data-viewer-focus]');
  const isolate = root.querySelector('[data-viewer-isolate]');
  const buttons = root.querySelectorAll('button[data-viewer-camera], [data-viewer-reset], [data-viewer-fit]');
  const inputs = [play, explode, focus, isolate, ...buttons].filter(Boolean);
  inputs.forEach(input => { input.disabled = true; });
  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  let renderer;
  let frameId = 0;
  let ready = false;

  function fail(message) {
    ready = false;
    cancelAnimationFrame(frameId);
    inputs.forEach(input => { input.disabled = true; });
    stage.dataset.viewerState = 'error';
    if (renderer) renderer.domElement.style.display = 'none';
    status.hidden = false;
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = 'Retry';
    retry.addEventListener('click', () => window.location.reload());
    status.replaceChildren(document.createTextNode(message + ' '), retry);
  }
  stage.dataset.viewerState = 'loading';
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
  } catch {
    fail('This browser could not start the 3D view.');
    return;
  }
  try {
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = .93;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    const canvas = renderer.domElement;
    canvas.setAttribute('aria-hidden', 'true');
    canvas.style.cssText = 'display:block;width:100%;height:100%;touch-action:none';
    stage.prepend(canvas);
    canvas.style.display = 'none';
    canvas.addEventListener('webglcontextlost', () => fail('The browser interrupted the 3D view.'));
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(34, 1, .015, 130);
    const controls = new OrbitControls(camera, canvas);
    controls.minDistance = .20;
    controls.maxDistance = 46;
    controls.enableDamping = true;
    controls.dampingFactor = .12;
    controls.rotateSpeed = .68;
    controls.zoomSpeed = .86;
    controls.panSpeed = .85;
    controls.minPolarAngle = .015;
    controls.maxPolarAngle = Math.PI - .015;
    const layers = new Map();
    let expansion = 0;
    let selected = 'all';
    let playing = !motionPreference.matches;
    let mixer;
    let goal = null;
    let expansionView = null;
    let visible = true;
    let last = 0;
    let quality = 0;
    let warmFrames = 0;
    let fastFrames = 0;
    let drawnFrames = 0;
    let qualityChanged = 0;
    const recentIntervals = [];
    const qualityScales = [1, .82, .65, .52];
    const up = new THREE.Vector3(0, 1, 0);
    const eyeDirection = () => camera.position.clone().sub(controls.target).normalize();

    function wake() {
      if (ready && visible && !document.hidden && !frameId) {
        frameId = requestAnimationFrame(draw);
      }
    }
    // Measure intervals only while frames are being requested continuously.
    // Ignore initial material compilation; a single hitch never drops quality.
    function adapt(interval, time) {
      if (!interval || ++warmFrames <= 4) return;
      recentIntervals.push(interval);
      if (recentIntervals.length > 12) recentIntervals.shift();
      const count = recentIntervals.length;
      const sustained = (threshold, required) => count >= required &&
        recentIntervals.slice(-required).every(seconds => seconds > threshold);
      let next = quality;
      if (sustained(.24, 4)) next = Math.min(3, quality + 2);
      else if (sustained(.082, 6) || (count === 12 && recentIntervals.filter(seconds => seconds > .044).length >= 10)) {
        next = Math.min(3, quality + 1);
      }
      fastFrames = interval < .024 ? fastFrames + 1 : 0;
      if (fastFrames > 210 && time - qualityChanged > 12000) next = Math.max(0, quality - 1);
      if (next === quality) return;
      quality = next;
      qualityChanged = time;
      fastFrames = 0;
      recentIntervals.length = 0;
      renderer.shadowMap.autoUpdate = quality === 0;
      renderer.shadowMap.needsUpdate = true;
      size();
    }

    function draw(time) {
      frameId = 0;
      if (!ready || !visible || document.hidden) { last = 0; return; }
      const elapsed = last ? Math.max(0, (time-last)/1000) : 0;
      last = time;
      adapt(elapsed, time);
      // The gear train keeps real time even when a low-power GPU skips poses.
      if (playing && mixer) mixer.update(elapsed);
      const smoothingDelta = Math.min(elapsed || 1/60, .45);
      if (goal) {
        const blend = motionPreference.matches ? 1 : 1 - Math.exp(-smoothingDelta * 9);
        controls.target.lerp(goal.target, blend);
        camera.position.lerp(goal.position, blend);
        if (camera.position.distanceToSquared(goal.position) < .000006 && controls.target.distanceToSquared(goal.target) < .000006) {
          camera.position.copy(goal.position);
          controls.target.copy(goal.target);
          goal = null;
        }
      }
      const moved = controls.update(smoothingDelta);
      drawnFrames++;
      if (quality && playing && drawnFrames % (quality * 5) === 0) renderer.shadowMap.needsUpdate = true;
      renderer.render(scene, camera);
      if (goal || moved || (mixer && playing)) wake();
      else last = 0;
    }

    function fit(direction, instant = false) {
      const right = new THREE.Vector3().crossVectors(up, direction).normalize();
      const vertical = new THREE.Vector3().crossVectors(direction, right);
      const points = [];
      for (const name of groups[selected]) {
        const layer = layers.get(name);
        if (!layer) continue;
        for (const p of layer.outline) {
          points.push(new THREE.Vector3(p.x, p.y + layer.rise * expansion, p.z));
        }
      }
      const center = new THREE.Box3().setFromPoints(points).getCenter(new THREE.Vector3());
      const min = new THREE.Vector2(Infinity, Infinity);
      const max = new THREE.Vector2(-Infinity, -Infinity);
      const offset = new THREE.Vector3();
      for (const p of points) {
        offset.copy(p).sub(center);
        const xy = new THREE.Vector2(right.dot(offset), vertical.dot(offset));
        min.min(xy);
        max.max(xy);
      }
      center.addScaledVector(right, (max.x+min.x)/2).addScaledVector(vertical, (max.y+min.y)/2);
      const tanHalfHeight = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
      const margin = selected === 'keeper' ? .54 : selected === 'all' ? .88 : .80;
      let distance = .24;
      for (const p of points) {
        offset.copy(p).sub(center);
        const projected = Math.max(Math.abs(offset.dot(right))/camera.aspect, Math.abs(offset.dot(vertical)));
        distance = Math.max(distance, offset.dot(direction) + projected/(tanHalfHeight*margin));
      }
      // Leave enough room around the keeper to see a tread and its neighbours.
      if (selected === 'keeper') distance = Math.max(1.18, distance);
      const position = center.clone().addScaledVector(direction, distance);
      if (instant || motionPreference.matches) {
        camera.position.copy(position);
        controls.target.copy(center);
        controls.update();
        goal = null;
      } else {
        goal = { target: center, position };
      }
      wake();
    }

    function setExpansion(amount) {
      expansion = THREE.MathUtils.clamp(amount, 0, 1);
      for (const { node, baseY, rise } of layers.values()) node.position.y = baseY + rise*expansion;
      renderer.shadowMap.needsUpdate = true;
      if (value) value.textContent = Math.round(expansion*100) + '%';
      if (!expansionView) expansionView = eyeDirection();
      const direction = expansionView.clone();
      // Open the angle slightly so separated layers read vertically.
      if (direction.y > .35 && selected === 'all') {
        direction.y = THREE.MathUtils.lerp(direction.y, .35, expansion);
        direction.normalize();
      }
      fit(direction);
    }

    function cameraAction(action, pan = false) {
      goal = null;
      expansionView = null;
      const offset = camera.position.clone().sub(controls.target);
      if (action === 'in' || action === 'out') {
        offset.setLength(THREE.MathUtils.clamp(offset.length()*(action === 'in' ? .82 : 1/.82), controls.minDistance, controls.maxDistance));
      } else if (pan) {
        const delta = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, action === 'up' || action === 'down' ? 1 : 0);
        const sign = action === 'up' || action === 'right' ? 1 : -1;
        controls.target.addScaledVector(delta, sign * offset.length() * .045);
      } else {
        const angles = new THREE.Spherical().setFromVector3(offset);
        if (action === 'left') angles.theta -= .14;
        if (action === 'right') angles.theta += .14;
        if (action === 'up') angles.phi -= .12;
        if (action === 'down') angles.phi += .12;
        angles.phi = THREE.MathUtils.clamp(angles.phi, controls.minPolarAngle, controls.maxPolarAngle);
        offset.setFromSpherical(angles);
      }
      camera.position.copy(controls.target).add(offset);
      controls.update();
      wake();
    }

    function size() {
      const width = stage.clientWidth;
      const height = stage.clientHeight;
      if (width < 1 || height < 1) return;
      const aspect = width / height;
      const oldAspect = camera.aspect;
      camera.aspect = aspect;
      camera.updateProjectionMatrix();
      const nativeRatio = Math.min(window.devicePixelRatio || 1, 1.65, Math.sqrt(1450000 / (width*height)));
      renderer.setPixelRatio(nativeRatio * qualityScales[quality]);
      renderer.setSize(width, height, false);
      if (ready && Math.abs(oldAspect-aspect) > .04) fit(eyeDirection(), true);
      wake();
    }

    function syncPlay() {
      const label = play?.querySelector('[data-viewer-play-label]') || play;
      if (label) label.textContent = playing ? 'Pause mechanism' : 'Play mechanism';
      if (play) play.setAttribute('aria-pressed', String(playing));
    }
    function syncIsolation() {
      const all = selected === 'all';
      if (isolate) {
        isolate.disabled = all;
        if (all) isolate.checked = false;
      }
      const visibleGroups = groups[selected];
      for (const [name, layer] of layers) {
        layer.node.visible = !isolate?.checked || visibleGroups.includes(name);
      }
      renderer.shadowMap.needsUpdate = true;
      wake();
    }

    function reset() {
      selected = 'all';
      expansion = 0;
      expansionView = defaultView.clone();
      if (focus) focus.value = 'all';
      syncIsolation();
      if (explode) explode.value = 0;
      setExpansion(0);
    }

    controls.addEventListener('start', () => { goal = null; expansionView = null; wake(); });
    controls.addEventListener('change', wake);
    canvas.addEventListener('pointerdown', () => stage.focus({ preventScroll: true }));
    stage.addEventListener('keydown', event => {
      if (!ready || event.altKey || event.ctrlKey || event.metaKey || ![stage, canvas].includes(event.target)) return;
      const action = { ArrowLeft:'left', ArrowRight:'right', ArrowUp:'up', ArrowDown:'down', '+':'in', '=':'in', '-':'out', '−':'out' }[event.key];
      if (action) {
        event.preventDefault();
        cameraAction(action, event.shiftKey);
      }
    });
    root.querySelectorAll('[data-viewer-camera]').forEach(button => button.addEventListener('click', () => cameraAction(button.dataset.viewerCamera)));
    root.querySelectorAll('[data-viewer-reset], [data-viewer-fit]').forEach(button => button.addEventListener('click', reset));
    explode?.addEventListener('input', () => setExpansion(Number(explode.value)/100));
    isolate?.addEventListener('change', syncIsolation);
    focus?.addEventListener('change', () => {
      selected = Object.hasOwn(groups, focus.value) ? focus.value : 'all';
      expansionView = (partViews[selected] || defaultView).clone();
      syncIsolation();
      fit(expansionView);
    });
    play?.addEventListener('click', () => {
      playing = !playing;
      renderer.shadowMap.needsUpdate = true;
      last = 0;
      syncPlay();
      wake();
    });
    new ResizeObserver(size).observe(stage);
    new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      last = 0;
      wake();
    }).observe(stage);
    document.addEventListener('visibilitychange', () => { last = 0; wake(); });
    size();
    const gltf = await new GLTFLoader().loadAsync('/model/sidereal.glb');
    if (stage.dataset.viewerState === 'error') return;
    const model = gltf.scene;
    scene.add(model);
    model.updateMatrixWorld(true);
    for (const [name, rise] of Object.entries(elevation)) {
      const node = model.getObjectByName(name);
      if (!node) throw new Error('Missing model part: ' + name);
      layers.set(name, { node, rise, baseY:node.position.y, outline:outline(node) });
    }
    model.traverse(node => {
      if (node.isMesh) {
        node.castShadow = true;
        node.receiveShadow = true;
      }
    });
    lightStudio(scene, renderer);
    const clip = gltf.animations.find(animation => animation.name === 'Sidereal Cycle') || gltf.animations[0];
    if (clip) {
      mixer = new THREE.AnimationMixer(model);
      mixer.clipAction(clip).play();
    } else {
      playing = false;
    }
    ready = true;
    inputs.forEach(input => { input.disabled = false; });
    if (play) play.disabled = !clip;
    syncIsolation();
    syncPlay();
    size();
    fit(defaultView, true);
    canvas.style.display = 'block';
    renderer.render(scene, camera);
    stage.dataset.viewerState = 'ready';
    status.hidden = true;
    wake();
  } catch (error) {
    console.error('The Sidereal Hour viewer', error);
    fail('The watch model could not be displayed.');
  }
}

const root = document.querySelector('[data-sidereal-viewer]');
if (root) initialize(root);

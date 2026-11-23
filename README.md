# The Sidereal Hour

A lantern keeper climbs into an observatory built inside a mechanical pocket watch. The 12-second Blender film and four-second loop each have an original stereo score.

[Open the 3D explorer](https://the-sidereal-hour.vercel.app/explore.html) to orbit the watch, play or pause the mechanism, spread the assembly apart, and isolate individual components. [Visit the gallery](https://the-sidereal-hour.vercel.app/) for the film and print plates.

- [Film](public/media/film.mp4) and [loop](public/media/loop.mp4), both 864 × 1080 at 24 fps
- [Poster](public/media/poster.jpg), [clean still](public/media/still.png), [detail plate](public/media/details.jpg), and [exploded assembly](public/media/exploded.jpg)
- [Editable Blender 4.3 scene](source/The%20Sidereal%20Hour.blend), including the cameras, animation, and packed score


## Local preview

Run these commands from the repository root, then visit `http://localhost:8000/explore.html`.

```sh
npm ci
npm run build
python3 -m http.server 8000 --directory public
```

The build bundles Three.js and the viewer into `public/viewer.js`. The browser loads the self-contained GLB when someone opens the explorer. The film gallery can be viewed without WebGL.

## Re-export the watch

The Blender project contains the full short film. The web export keeps the four-second mechanism cycle, places the keeper on the steps, and divides the geometry into seven independently movable assemblies. To regenerate it with Blender 4.3, run the following command from the repository root.

```sh
blender -b "source/The Sidereal Hour.blend" --python-exit-code 1 -P source/sidereal_export.py -- public/model/sidereal.glb
```

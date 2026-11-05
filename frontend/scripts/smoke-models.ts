/** Run after npm test, with the normal frontend and Python servers running. */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import * as THREE from 'three'
import { parseModel, validateMesh } from '../src/modelImport.ts'

const base = process.env.SKIPPY_URL || 'http://127.0.0.1:5173'
assert.ok((await fetch(`${base}/api/health`)).ok, 'Start the Python API and frontend first.')
const camera = new THREE.PerspectiveCamera(45, 1000 / 700, 0.1, 100)
camera.position.set(0, 0, 6)
camera.lookAt(0, 0, 0)
camera.updateMatrixWorld(true)
const points = Array.from({ length: 80 }, (_, i) => {
  const x = -2.4 + (4.8 * i) / 79
  const point = new THREE.Vector3(x, 0.1 * Math.sin(x * 2), 0).project(camera)
  return [(point.x + 1) * 500, (1 - point.y) * 350]
})
for (const filename of ['cube.ply', 'cube-binary.ply', 'two-cubes.glb']) {
  const bytes = await readFile(
    new URL(`../node_modules/.tmp/model-import/${filename}`, import.meta.url),
  )
  const mesh = await parseModel(
    bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    filename,
  )
  // The same geometry is self-contained in saved JSON and offline solve inputs.
  validateMesh(JSON.parse(JSON.stringify(mesh)))
  const response = await fetch(`${base}/api/solve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      objects: [
        {
          id: 'mesh',
          kind: 'mesh',
          position: [0, 0, 0],
          quaternion: [0, 0, 0, 1],
          size: [1, 1, 1],
          enabled: true,
          guide: false,
          mesh: { vertices: mesh.vertices, faces: mesh.faces },
        },
      ],
      camera: {
        world: camera.matrixWorld.toArray(),
        projection: camera.projectionMatrix.toArray(),
        width: 1000,
        height: 700,
      },
      points,
      options: { iterations: 8, max_points: 80 },
      revision: 7,
      request_id: filename,
    }),
  })
  assert.ok(response.ok, await response.clone().text())
  const result = await response.json()
  assert.equal(result.status, 'ok', JSON.stringify(result))
  assert.equal(result.request_id, filename)
  assert.equal(result.revision, 7)
  assert.ok(result.points.length > 2)
  assert.ok(result.debug.on.some(Boolean) && result.debug.on.some((on: boolean) => !on))
  assert.ok(result.metrics.projection_p95_px < 3)
  console.log(
    `${filename}: ${mesh.faces.length} faces, ${result.points.length} curve points, projection P95 ${result.metrics.projection_p95_px.toFixed(3)} px`,
  )
}

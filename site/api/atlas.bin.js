// GET /api/atlas.bin: the run's neuron atlas (positions and classes) for the neural replay.
import { addressedHandler } from "./_addressed.js";

export default addressedHandler({
  key: "atlas_sha256", dir: "atlas", ext: "bin", live: "/api/atlas.bin",
  contentType: "application/octet-stream", magic: Buffer.from("SFAT"),
});

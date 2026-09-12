// GET /api/activity.bin: the latest observation's binned spikes for the neural replay.
import { addressedHandler } from "./_addressed.js";

export default addressedHandler({
  key: "activity_sha256", dir: "activity", ext: "bin", live: "/api/activity.bin",
  contentType: "application/octet-stream", magic: Buffer.from("SFAC"),
});

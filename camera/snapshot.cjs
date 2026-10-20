// Read-only snapshot helper. Configuration and tokens live outside the checkout.
const fs = require('node:fs');
const path = require('node:path');
process.env.DEBUG = '';
process.umask(0o077);

const delay = (milliseconds) => new Promise(resolve => setTimeout(resolve, milliseconds));
const write = (payload) => new Promise(resolve => process.stdout.write(JSON.stringify(payload) + '\n', resolve));
const snapshotTimestamps = new WeakMap();

function retryDelay(error, failures, interval) {
  const header = error?.response?.headers?.['retry-after'];
  let retryAfter = Number(header);
  if (!Number.isFinite(retryAfter)) retryAfter = (Date.parse(header) - Date.now()) / 1000;
  return Math.max(interval, Number.isFinite(retryAfter) ? retryAfter : 0,
    error?.code === 'NO_RECENT_IMAGE' ? interval : Math.min(300, interval * 2 ** Math.min(failures - 1, 5)));
}

async function capture(client, cameraID, configuration = {}) {
  const force = configuration.force_snapshots === true;
  const previous = snapshotTimestamps.get(client)?.get(String(cameraID));
  // Ring's next-image endpoint can return a new uploaded frame immediately.
  // Asking for only the last three seconds needlessly waits for another one.
  const after = force ? Math.max(previous ?? -Infinity, Date.now() - 10000) : 0;
  const snapshot = await client.request({
    url: `https://app-snaps.ring.com/snapshots/next/${cameraID}`,
    responseType: 'buffer',
    searchParams: { 'after-ms': after,
      'max-wait-ms': force ? 15000 : 1000, extras: force ? 'force' : undefined },
    headers: { accept: 'image/jpeg' },
    allowNoResponse: true,
    timeout: 18000,
  });
  if (!Buffer.isBuffer(snapshot) || snapshot.length === 0 || !Number.isFinite(snapshot.responseTimestamp) || !Number.isFinite(snapshot.timeMillis)) {
    throw Object.assign(new Error('No recent camera image'), { code: 'NO_RECENT_IMAGE' });
  }
  if (!snapshotTimestamps.has(client)) snapshotTimestamps.set(client, new Map());
  snapshotTimestamps.get(client).set(String(cameraID), Math.max(previous ?? -Infinity, snapshot.timeMillis));
  const age = Math.abs(snapshot.responseTimestamp - snapshot.timeMillis);
  return { image: snapshot.toString('base64'), captured_at: (Date.now() - age) / 1000 };
}

async function main() {
  const { RingRestClient } = require('ring-client-api/rest-client');
  const { useLogger } = require('ring-client-api/util');
  useLogger({ logInfo() {}, logError() {} });
  const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const watching = process.argv.includes('--watch');
  const interval = Number(config.poll_interval_seconds ?? 10);
  if (!Number.isFinite(interval) || interval < 10) throw new Error('Invalid camera interval');
  const cameraID = String(config.camera_id);
  if (!/^\d+$/.test(cameraID)) throw new Error('Invalid camera ID');
  const stored = JSON.parse(fs.readFileSync(config.token_file, 'utf8'));
  const client = new RingRestClient({ refreshToken: stored.refreshToken, controlCenterDisplayName: 'Gate status camera' });
  client.onRefreshTokenUpdated.subscribe(({ newRefreshToken }) => {
    const temporary = path.join(path.dirname(config.token_file), `.ring-token-${process.pid}.tmp`);
    fs.writeFileSync(temporary, JSON.stringify({ refreshToken: newRefreshToken, lastUpdated: new Date().toISOString() }), { mode: 0o600 });
    fs.renameSync(temporary, config.token_file);
  });
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => { client.clearTimeouts(); process.exit(0); });
  let failures = 0;
  try {
    do {
      const started = Date.now();
      try {
        await write(await capture(client, cameraID, config));
        failures = 0;
        if (watching) await delay(Math.max(0, interval * 1000 - (Date.now() - started)));
      } catch (error) {
        if (!watching) throw error;
        const seconds = retryDelay(error, ++failures, interval);
        await write({ error: 'camera_unavailable', retry_after: seconds });
        await delay(seconds * 1000);
      }
    } while (watching);
  } finally {
    client.clearTimeouts();
  }
}

module.exports = { retryDelay, capture };
if (require.main === module) main().then(() => process.exit(0)).catch(() => {
    console.error('Camera snapshot unavailable');
    process.exit(1);
  });

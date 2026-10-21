from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "requires Node.js")
class CameraHelperTests(unittest.TestCase):
    def test_failure_diagnostics_include_only_known_codes_and_http_status(self):
        helper = Path(__file__).resolve().parents[1] / "camera/snapshot.cjs"
        script = """const assert = require('node:assert/strict');
const { failureDetails } = require(process.argv[1]);
assert.deepEqual(failureDetails({code:'ETIMEDOUT',response:{statusCode:429}}),{error_code:'ETIMEDOUT',error_status:429});
assert.deepEqual(failureDetails({code:'private-token',message:'private-url',response:{statusCode:'private-body',headers:{authorization:'secret'}}}),{error_code:'OTHER',error_status:0});
"""
        subprocess.run([shutil.which("node"), "-e", script, str(helper)], check=True, capture_output=True, timeout=10)

    def test_backoff_caps_exponential_retries_and_respects_ring_retry_after(self):
        helper = Path(__file__).resolve().parents[1] / "camera/snapshot.cjs"
        script = """const assert = require('node:assert/strict');
const { retryDelay } = require(process.argv[1]);
assert.equal(retryDelay({}, 1, 10), 10);
assert.equal(retryDelay({}, 2, 10), 20);
assert.equal(retryDelay({}, 10, 10), 300);
assert.equal(retryDelay({response:{headers:{'retry-after':'120'}}}, 1, 10), 120);
Date.now = () => 0;
assert.equal(retryDelay({response:{headers:{'retry-after':'Thu, 01 Jan 1970 00:10:00 GMT'}}}, 1, 10), 600);
"""
        subprocess.run([shutil.which("node"), "-e", script, str(helper)], check=True, capture_output=True, timeout=10)

    def test_passive_capture_never_forces_camera_and_preserves_old_capture_time(self):
        helper = Path(__file__).resolve().parents[1] / "camera/snapshot.cjs"
        script = """const assert = require('node:assert/strict');
const { capture, retryDelay } = require(process.argv[1]);
Date.now = () => 100000;
let options;
const frame = Object.assign(Buffer.from([0xff,0xd8,0xff]), {responseTimestamp:100000,timeMillis:10000});
const client={request:async value=>{options=value; return frame}};
(async()=>{
 const result=await capture(client,'123');
 assert.equal(options.searchParams['after-ms'],0);
 assert.equal(options.searchParams.extras,undefined);
 assert.equal(result.captured_at,10); // Never relabel an old image as a new capture.
 await assert.rejects(capture(client,'123',{force_snapshots:true}),{code:'NO_RECENT_IMAGE'});
 assert.equal(options.searchParams.extras,'force');
 assert.equal(options.searchParams['after-ms'],90000);
 await assert.rejects(capture({request:async()=>Buffer.alloc(0)},'123'),{code:'NO_RECENT_IMAGE'});
 assert.equal(retryDelay({code:'NO_RECENT_IMAGE'},20,10),10);
 assert.equal(retryDelay({code:'NO_RECENT_IMAGE',response:{headers:{'retry-after':'90'}}},20,10),90);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        subprocess.run([shutil.which("node"), "-e", script, str(helper)], check=True, capture_output=True, timeout=10)

    def test_powered_capture_accepts_new_uploaded_frames_without_replaying_old_ones(self):
        helper = Path(__file__).resolve().parents[1] / "camera/snapshot.cjs"
        script = """const assert = require('node:assert/strict');
const { capture } = require(process.argv[1]);
let now=100000, timestamp=94000;
Date.now=()=>now;
const client={request:async options=>{
 if(timestamp<=options.searchParams['after-ms']) return Buffer.alloc(0);
 return Object.assign(Buffer.from([0xff,0xd8,0xff]),{responseTimestamp:now,timeMillis:timestamp});
}};
(async()=>{
 // Ring already has a six-second-old frame. No extra capture is needed.
 assert.equal((await capture(client,'123',{force_snapshots:true})).captured_at,94);
 now=101000;
 await assert.rejects(capture(client,'123',{force_snapshots:true}),{code:'NO_RECENT_IMAGE'});
 timestamp=99000;
 assert.equal((await capture(client,'123',{force_snapshots:true})).captured_at,99);
 // A camera's cursor must not affect another camera on the same Ring client.
 timestamp=94000;
 assert.equal((await capture(client,'456',{force_snapshots:true})).captured_at,94);
 // After a long gap, never ask Ring to replay an old buffered image.
 now=200000;timestamp=150000;
 await assert.rejects(capture(client,'123',{force_snapshots:true}),{code:'NO_RECENT_IMAGE'});
})().catch(error=>{console.error(error);process.exit(1)});
"""
        subprocess.run([shutil.which("node"), "-e", script, str(helper)], check=True, capture_output=True, timeout=10)

    def test_next_image_404_can_recover_an_independent_uploaded_frame(self):
        helper = Path(__file__).resolve().parents[1] / "camera/snapshot.cjs"
        script = """const assert = require('node:assert/strict');
const { capture } = require(process.argv[1]);
Date.now=()=>100000;
let timestamp=85000, retryAfter, requests=[];
const client={request:async options=>{
 requests.push(options);
 if(options.searchParams['after-ms']!==0) throw {response:{statusCode:404,headers:{'retry-after':retryAfter}}};
 return Object.assign(Buffer.from([0xff,0xd8,0xff]),{responseTimestamp:100000,timeMillis:timestamp});
}};
(async()=>{
 assert.equal((await capture(client,'123',{force_snapshots:true})).captured_at,85);
 assert.equal(requests[0].searchParams.extras,'force');
 assert.equal(requests[1].searchParams.extras,undefined);
 await assert.rejects(capture(client,'123',{force_snapshots:true}),{code:'NO_RECENT_IMAGE'});
 timestamp=50000; // The original old capture time must survive the fallback.
 assert.equal((await capture(client,'456',{force_snapshots:true})).captured_at,50);
 requests=[];retryAfter='90';
 await assert.rejects(capture(client,'789',{force_snapshots:true}));
 assert.equal(requests.length,1);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        subprocess.run([shutil.which("node"), "-e", script, str(helper)], check=True, capture_output=True, timeout=10)

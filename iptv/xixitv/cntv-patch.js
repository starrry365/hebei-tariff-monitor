// Undo ChinaDRM on CCTV segments in the browser, without being able to decrypt.
//
// The player fetches segments from the CDN and asks our /cntv/patch endpoint
// for the plaintext of the bytes that were encrypted — roughly 11-17% of a
// segment (see docs/cntv-drm-research.md §7). On a busy channel the first
// couple of segments may instead come from /cntv/seg/, which holds the same
// encrypted CDN bytes. The patch still describes those bytes. Decryption
// itself stays on the server: everything here is offset arithmetic.
//
// Wire format (built by internal/cntvpatch):
//   "XPT1" | u32 segment length | u32 CRC-32 of the segment | u32 run count
//   run count × (varint gap since previous run, varint run length)
//   the runs' plaintext bytes, in order
(function () {
  "use strict";

  var MAGIC = 0x58505431; // "XPT1"
  var HEADER = 16;

  var crcTable = null;
  function crc32(bytes) {
    if (!crcTable) {
      crcTable = new Int32Array(256);
      for (var n = 0; n < 256; n++) {
        var c = n;
        for (var k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        crcTable[n] = c;
      }
    }
    var crc = -1;
    for (var i = 0; i < bytes.length; i++) {
      crc = (crc >>> 8) ^ crcTable[(crc ^ bytes[i]) & 0xff];
    }
    return (crc ^ -1) >>> 0;
  }

  // segmentName is the part of a segment URL that identifies which segment it is.
  // The server matches it against the template it used to build the playlist, so
  // it must be exactly the last path element without the query.
  function segmentName(url) {
    var s = String(url);
    var cut = s.search(/[?#]/);
    if (cut >= 0) s = s.slice(0, cut);
    var slash = s.lastIndexOf("/");
    return slash >= 0 ? s.slice(slash + 1) : s;
  }

  // patchSlug returns the channel slug for a synthesized playlist URL, or "".
  function patchSlug(playlistURL) {
    var m = /\/cntv\/live\/([^/?#.]+)/.exec(String(playlistURL));
    return m ? m[1] : "";
  }

  function applyPatch(segment, patch) {
    if (patch.length < HEADER) throw new Error("patch too short");
    var view = new DataView(patch.buffer, patch.byteOffset, patch.byteLength);
    if (view.getUint32(0) !== MAGIC) throw new Error("unknown patch format");
    var segLen = view.getUint32(4);
    var segCRC = view.getUint32(8);
    var runCount = view.getUint32(12);
    if (segment.length !== segLen) throw new Error("segment length mismatch");

    // Both sides must be looking at the same bytes. A CDN edge that served the
    // player something else turns into a clean failure instead of a scrambled
    // picture.
    if (crc32(segment) !== segCRC) throw new Error("segment checksum mismatch");

    // Read the run table, then the data that follows it.
    var starts = new Uint32Array(runCount);
    var lengths = new Uint32Array(runCount);
    var pos = HEADER;
    var offset = 0;
    var total = 0;
    for (var i = 0; i < runCount; i++) {
      var gap = 0;
      var shift = 0;
      var b;
      do {
        if (pos >= patch.length) throw new Error("truncated run table");
        b = patch[pos++];
        gap += (b & 0x7f) * Math.pow(2, shift);
        shift += 7;
      } while (b & 0x80);
      var len = 0;
      shift = 0;
      do {
        if (pos >= patch.length) throw new Error("truncated run table");
        b = patch[pos++];
        len += (b & 0x7f) * Math.pow(2, shift);
        shift += 7;
      } while (b & 0x80);
      starts[i] = offset + gap;
      lengths[i] = len;
      offset = starts[i] + len;
      total += len;
      if (offset > segLen) throw new Error("run past the end of the segment");
    }
    if (patch.length - pos !== total) throw new Error("patch data size mismatch");

    for (var j = 0; j < runCount; j++) {
      segment.set(patch.subarray(pos, pos + lengths[j]), starts[j]);
      pos += lengths[j];
    }
    return segment;
  }

  // The CDN reuses the same segment counter across every rendition of the
  // bitrate ladder, so a bare number does not identify the bytes: 281771.ts is
  // a different size in the 480p and 1080p renditions. Send the exact segment
  // URL too (the "u" param) so the server can pick the rendition that
  // actually produces it, rather than guessing — hls.js's level index is not
  // usable for this: it sorts levels by bitrate, which does not match the
  // order our own playlist lists them in (see handleCNTVLive).
  // segmentBytes copies the loaded segment into a fresh Uint8Array.
  // ArrayBuffer and any typed-array view are both accepted. A view into a
  // larger buffer is copied by range, so the result's .buffer is exactly the
  // segment hls.js should transmux.
  function segmentBytes(data) {
    if (data instanceof ArrayBuffer) return new Uint8Array(data.slice(0));
    if (ArrayBuffer.isView(data)) {
      return new Uint8Array(
        data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)
      );
    }
    return null;
  }

  // /cntv/seg/<slug>/<file>?u=<cdn> is our copy of a CDN segment. The patch
  // is keyed by the CDN URL, which is carried in "u".
  function patchSegmentURL(segmentURL) {
    try {
      var abs = new URL(segmentURL, location.origin);
      if (abs.origin === location.origin && abs.pathname.indexOf("/cntv/seg/") === 0) {
        var src = abs.searchParams.get("u");
        if (src) return src;
      }
    } catch (e) {}
    return segmentURL;
  }

  function fetchPatch(slug, segmentURL) {
    var target = patchSegmentURL(segmentURL);
    var url =
      "/cntv/patch/" +
      encodeURIComponent(slug) +
      "/" +
      encodeURIComponent(segmentName(target)) +
      "?u=" +
      encodeURIComponent(target);
    return fetch(url, { credentials: "same-origin" }).then(function (res) {
      if (!res.ok) throw new Error("patch HTTP " + res.status);
      return res.arrayBuffer();
    });
  }

  // makePatchLoader wraps whichever fragment loader hls.js ended up with — the
  // default one, or the P2P engine's. Peers keep sharing the encrypted segments
  // they already have; patching happens after loading, so P2P is unaffected.
  //
  // This has to subclass rather than borrow the base constructor: with P2P on,
  // the loader is a native ES6 class, and calling one without `new` throws
  // ("Class constructors cannot be invoked without 'new'"). hls.js reports that
  // as a fragment error on every segment, so the channel never plays. Extending
  // works for both that and hls.js's own transpiled loader, and it carries the
  // P2P loader's statics across too.
  function makePatchLoader(Base, resolveSlug, handlers) {
    handlers = handlers || {};
    return class PatchLoader extends Base {
      load(context, config, callbacks) {
        var url = context && context.url;
        // Which playlist is playing can change under us: the app falls back to
        // a mirror by calling loadSource on this same instance, and the loader
        // stays installed. Only segments of a synthesized /cntv/live playlist
        // are ours to patch — a mirror's are ordinary bytes, and asking for
        // patches for them would fail every one of them.
        var slug = resolveSlug();
        // fLoader only ever loads fragments, but be explicit: an EXT-X-MAP init
        // segment or a key would not be patchable.
        var isSegment = url && (!!context.frag || /\.ts(\?|$)/i.test(url)) && !/\.(key|mp4|m4s)(\?|$)/i.test(url);
        if (!slug || !isSegment) {
          super.load(context, config, callbacks);
          return;
        }

        // Start the patch request now rather than after the segment arrives: the
        // two are independent, and this keeps the added latency near zero.
        // Settle it immediately. A 502 can come back before the segment does,
        // and a rejection nobody is listening to yet is an uncaught error that
        // never reaches hls.js.
        var patchResult = fetchPatch(slug, url).then(
          function (buf) {
            return { buf: buf };
          },
          function (err) {
            return { err: err };
          }
        );
        var onSuccess = callbacks.onSuccess;
        var wrapped = Object.create(callbacks);
        wrapped.onSuccess = function (response, stats, ctx, networkDetails) {
          // hls.js hands over an ArrayBuffer. The P2P loader hands over a
          // Uint8Array (a copy of the encrypted bytes it will keep sharing).
          // Treating only ArrayBuffer as patchable played those peer bytes
          // still scrambled.
          var segment = segmentBytes(response && response.data);
          if (!segment) {
            onSuccess(response, stats, ctx, networkDetails);
            return;
          }
          // Handing over scrambled bytes would look like a decoder bug, so
          // fail the fragment cleanly instead. Tell the app too: hls.js
          // treats this as a network error and spends about a minute
          // re-downloading whole segments before it gives up, but a patch
          // that cannot be built is not going to succeed on retry — there is
          // nothing to wait for.
          var fail = function (err) {
            if (window.console) console.warn("cntv patch failed", err && err.message);
            // A missing segment (CDN 404) is this one file, not a dead
            // decryptor. Counting it would abandon the official stream after
            // a few expired fragments.
            var missing = err && /patch HTTP 404/.test(String(err.message));
            if (!missing && handlers.onPatchFailed) handlers.onPatchFailed(err);
            if (callbacks.onError) {
              callbacks.onError({ code: 0, text: "patch failed: " + (err && err.message) }, ctx, networkDetails, stats);
            }
          };
          patchResult.then(function (result) {
            if (result.err) {
              fail(result.err);
              return;
            }
            // applyPatch can throw (length/checksum mismatch, truncated table):
            // catch it here so it reaches the same clean-failure path as a
            // rejected fetch, instead of becoming an unhandled rejection that
            // never calls onSuccess/onError and leaves the fragment hanging.
            // Patch a copy. The P2P cache must keep the encrypted bytes so the
            // next peer can apply the same patch; writing into its buffer would
            // decrypt the copy everyone else downloads.
            try {
              applyPatch(segment, new Uint8Array(result.buf));
            } catch (err) {
              fail(err);
              return;
            }
            response.data = segment.buffer;
            if (handlers.onPatched) handlers.onPatched();
            onSuccess(response, stats, ctx, networkDetails);
          });
        };
        super.load(context, config, wrapped);
      }
    };
  }

  // installPatchLoader hooks an hls.js instance whose playlist is one of our
  // synthesized /cntv/live ones. Safe to call unconditionally.
  //
  // playlistURL may be a function, in which case it is asked again for every
  // segment. Pass one when the app can switch the instance to another source,
  // so patching stops as soon as it does.
  //
  // handlers.onPatched / handlers.onPatchFailed report whether patching is
  // working, so the caller can stop using a stream it cannot decode instead of
  // waiting for hls.js to exhaust its retries.
  function installPatchLoader(hls, playlistURL, handlers) {
    if (!hls || !hls.config) return false;
    var resolveSlug =
      typeof playlistURL === "function"
        ? function () {
            return patchSlug(playlistURL());
          }
        : function () {
            return patchSlug(playlistURL);
          };
    if (!resolveSlug()) return false;
    var Base = hls.config.fLoader || hls.config.loader;
    if (typeof Base !== "function") return false;
    hls.config.fLoader = makePatchLoader(Base, resolveSlug, handlers);
    return true;
  }

  window.cntvPatch = {
    applyPatch: applyPatch,
    crc32: crc32,
    segmentName: segmentName,
    patchSlug: patchSlug,
    installPatchLoader: installPatchLoader,
  };
})();

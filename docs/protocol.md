# key-cli machine protocol

`key-cli` owns the machine-facing JSON emitted by the `key` command. This document
describes the stable external contract consumed by Clavis. A breaking response change
requires updates here and a corresponding public-contract test; Python module layout and
function names are not part of the protocol.

## Envelope

JSON output is requested with `--json` or `--format json` where the command supports the
latter. Responses contain these common fields:

- `schemaVersion`: integer, currently `1`;
- `command`: the public operation name, such as `record.status` or `clipboard.list`;
- `ok`: boolean matching whether the command succeeded;
- `error`: `null` on success, otherwise an object with at least `code` and `message`;
- `exitCode` may be present when a delegated process has its own result code.

Consumers must reject an unknown `schemaVersion` instead of guessing field meanings. Error
objects may include a `details` mapping with command-specific information.

## Commands and state

The stable command names currently exposed by JSON responses are:

- `version` and `doctor` for metadata and dependency diagnostics;
- `shell.start`, `shell.kill`, `shell.log` and `shell.ipc` for Quickshell lifecycle actions;
- `ipc.show` and `ipc.call` for public Quickshell IPC forwarding;
- `record.start`, `record.status`, `record.pause`, `record.resume` and `record.stop`;
- `audio.start`, `audio.status` and `audio.stop`;
- `clipboard.status`, `clipboard.list`, `clipboard.inspect`, `clipboard.restore`,
  `clipboard.delete`, `clipboard.clear`, `clipboard.watch` and `clipboard.store`.

Recording and audio responses include a versioned state object. Stable state fields include
`state`, `sessionId`, `pid`, `processStartTicks`, `processStartedAtMs`, `startedAtMs`,
`completedAtMs`, `updatedAtMs`, `temporaryPath`, `outputPath` and `error` when applicable.
The recording command additionally reports `type`, `target`, `fps` and `audio`; audio
reports the selected source and final duration when available. Paths are external paths,
not implementation-specific temporary object names.

Clipboard responses report the selected operation, dependency/capability information,
watcher state and, for entries, the stable `id`, MIME/payload classification and decoded
metadata. Binary payload data is not embedded in the normal JSON response.

Text entries are exposed as literal plain text. When a clipboard offer contains both
`text/plain` and `text/html`, the plain-text representation is stored. HTML-only source is
stored byte-for-byte and exposed as literal text within the normal preview/search limits; it
is not rendered, stripped, entity-decoded or promoted to an embedded image. Markdown,
CSS, CSV, XML, JSON and other supported text follow the same literal-content rule.
Restoring UTF-8 text publishes `text/plain;charset=utf-8`. No charset transcoding
is performed; bytes that cannot be safely interpreted as UTF-8 remain binary.

## Clipboard capabilities and representation

The clipboard capability object retains the existing fields and adds explicit
representation limits, without changing `schemaVersion: 1`:

```json
{
  "inspect": true,
  "preview": true,
  "mimeRestore": true,
  "mimeAwareStore": true,
  "singleRepresentation": true,
  "multiMime": false,
  "originalMimePreserved": false
}
```

`mimeAwareStore` means MIME-guided selection of one representation. cliphist owns
history, deduplication, limits, deletion and the saved payload bytes. key-cli does
not persist the original MIME, the other offered representations, or a MIME sidecar.
`selectedMime` describes only that store operation, not persistent entry metadata.

`mimeRestore` means semantic restoration: key-cli classifies decoded bytes using
image signatures, supported file-list syntax or safe UTF-8 text, then publishes one
appropriate type through wl-copy. It does not reproduce the original MIME offer.
Image data and GNOME copy/cut/file-list payloads retain their bytes; textual formats
are displayed literally and restored as plain text. Preview truncation and display
summaries never modify the saved payload. The classification may differ from the
original type, especially when text itself contains valid file-list syntax.

Consumers such as Clavis should keep validating the envelope and the capabilities
they use, accept additive capability fields, and display clipboard text with an
explicit plain-text mode. Missing new fields on older key-cli builds do not imply
support for multi-MIME or original MIME preservation.

## Clipboard capture

`key clipboard watch` keeps one `wl-paste --watch` process. Each callback applies
key's MIME priority to the current offer: file lists, supported images, plain text,
then Markdown, HTML, other `text/*`, and the known textual application types
`application/json`, `application/xml`, `application/xhtml+xml`. MIME matching is
case-insensitive and accepts parameters; UTF-8 variants are preferred within a
type, and the original offered name is passed to wl-paste. A matching, supported `CLIPBOARD_TYPE` reuses the callback's stdin bytes;
a preferred representation is read explicitly with `wl-paste --no-newline --type`.
Direct `clipboard store` uses the same priority and never appends a newline.

If the offer query fails, the captured MIME disappears, or the preferred read
fails, a supported stdin representation is retained. Without usable captured data,
unsupported offers and read failures return an error. Sensitive, cleared and empty
events are not stored. Payload size limits still apply before writing to cliphist.

The watch callback and a subsequent `wl-paste` query are not an atomic snapshot.
A rapid copy can replace the offer between those operations, even when MIME names
are unchanged. This adapter does not promise original-offer identity or implement
its own Wayland data-control client to eliminate that race.

## Exit codes

The process exit code is part of the contract:

- `0`: success;
- `1`: general backend failure;
- `2`: usage or argument error;
- `3`: required dependency unavailable;
- `4`: another recording/session operation is active;
- `5`: invalid or unavailable saved state;
- `6`: recorder failed to start;
- `7`: recorder failed to stop safely;
- `8`: recording/audio post-processing failed.

The JSON `ok` value and the exit code must agree. Dependency and state errors still return
the standard envelope so callers can report a useful error without parsing human text.

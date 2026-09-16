// Packet-confined tools for the restricted Reviewer.
//
// The reviewer session has NO built-in tools: no read/bash/edit/write, no
// extensions, no context files. These two tools are its only way to see
// anything, and they can only resolve inside the generated review packet.
// Path confinement is enforced here, in our code, not by trusting pi's read
// tool (which accepts absolute paths) or by prompt.
import { realpathSync, readFileSync, readdirSync, statSync } from "node:fs";
import { basename, extname, isAbsolute, join, resolve, sep } from "node:path";
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";
type TextContent = { type: "text"; text: string };
type ImageContent = { type: "image"; data: string; mimeType: string };

const TEXT_LIMIT = 1_500_000;
const IMAGE_LIMIT = 20 * 1024 * 1024;
const IMAGE_MIME: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
};

function inside(root: string, relative: string): string {
  if (!relative || isAbsolute(relative) || relative.includes("\0")) {
    throw new Error("packet paths are relative to the packet root");
  }
  const resolved = resolve(root, relative);
  const rootWithSep = root.endsWith(sep) ? root : root + sep;
  if (resolved !== root && !resolved.startsWith(rootWithSep)) {
    throw new Error("packet_read only resolves inside the review packet");
  }
  // Every component must be a real file inside the packet; a symlink pointing
  // out of the packet would smuggle in live workspace state.
  const real = realpathSync(resolved);
  if (real !== resolved && !real.startsWith(rootWithSep)) {
    throw new Error("packet_read refuses symlink escapes");
  }
  if (!statSync(real).isFile()) {
    throw new Error(`not a packet file: ${relative}`);
  }
  return real;
}

function walk(dir: string, root: string, out: string[]): void {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) continue; // packets must not contain links (checked again at build)
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full, root, out);
    else if (entry.isFile()) out.push(full.slice(root.length + 1).split(sep).join("/"));
  }
}

export function packetTools(packetRoot: string) {
  const root = realpathSync(packetRoot);

  const packetList = defineTool({
    name: "packet_list",
    label: "List packet files",
    description:
      "List every file in this fixed review packet (paths relative to the packet root). " +
      "The packet is the only authority you may read.",
    parameters: Type.Object({}),
    execute: async () => {
      const files: string[] = [];
      walk(root, root, files);
      files.sort();
      const text = files.join("\n");
      return { content: [{ type: "text", text } satisfies TextContent] as (TextContent | ImageContent)[], details: { count: files.length } as Record<string, unknown> };
    },
  });

  const packetRead = defineTool({
    name: "packet_read",
    label: "Read a packet file",
    description:
      "Read one file from the fixed review packet. path is relative to the packet root " +
      "(e.g. target.json, sources/<id>.txt, history/responses.json, images/...). " +
      "Text is returned as text; packet images are returned as image content. " +
      "You cannot read anything outside this packet.",
    parameters: Type.Object({
      path: Type.String({ description: "Packet-relative path" }),
      start_line: Type.Optional(Type.Number({ description: "1-based first line for long text files" })),
      end_line: Type.Optional(Type.Number({ description: "1-based last line (inclusive)" })),
    }),
    execute: async (_id, params) => {
      const file = inside(root, params.path);
      const mime = IMAGE_MIME[extname(file).toLowerCase()];
      if (mime) {
        const bytes = readFileSync(file);
        if (bytes.length > IMAGE_LIMIT) throw new Error(`image exceeds ${IMAGE_LIMIT} bytes`);
        const image: ImageContent = { type: "image", data: bytes.toString("base64"), mimeType: mime };
        return {
          content: [{ type: "text", text: `${params.path} (${bytes.length} bytes)` } satisfies TextContent, image],
          details: { image: true, bytes: bytes.length } as Record<string, unknown>,
        };
      }
      const raw = readFileSync(file);
      if (raw.length > TEXT_LIMIT) {
        const head = raw.subarray(0, TEXT_LIMIT).toString("utf-8");
        return {
          content: [{ type: "text", text: head + `\n[truncated: file is ${raw.length} bytes; re-read with start_line/end_line]` }],
          details: { truncated: true, bytes: raw.length } as Record<string, unknown>,
        };
      }
      let text = raw.toString("utf-8");
      const total = text.split("\n").length;
      if (params.start_line !== undefined || params.end_line !== undefined) {
        const from = Math.max(1, params.start_line ?? 1);
        const to = Math.min(total, params.end_line ?? total);
        text = text.split("\n").slice(from - 1, to).join("\n");
        text = `[lines ${from}-${to} of ${total}]\n` + text;
      }
      return { content: [{ type: "text", text }], details: { lines: total } as Record<string, unknown> };
    },
  });

  return [packetList, packetRead];
}

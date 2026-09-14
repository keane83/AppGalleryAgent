#!/usr/bin/env node
/**
 * HarmonyOS build-profile 密码加解密（独立脚本，逻辑来自 hvigor DecipherUtil）。
 * 用法：
 *   node harmony_signing_password.js encrypt <materialDir> <plaintext>
 *   node harmony_signing_password.js decrypt <materialDir> <hex>
 */
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const COMPONENT = Int8Array.from([
  49, 243, 9, 115, 214, 175, 91, 184, 211, 190, 177, 88, 101, 131, 192, 119,
]);
const DIRS = ["fd", "ac", "ce"];
const MAC_DS_STORE = ".DS_Store";

function readDirBytes(dir) {
  const files = fs.readdirSync(dir).filter((name) => name !== MAC_DS_STORE);
  if (files.length !== 1) {
    throw new Error(`material 目录文件数量异常: ${dir}`);
  }
  return new Int8Array(fs.readFileSync(path.join(dir, files[0])));
}

function xor(a, b) {
  if (a.byteLength !== b.byteLength) {
    throw new Error("material 组件长度不一致");
  }
  const out = new Int8Array(a.byteLength);
  for (let i = 0; i < a.byteLength; i += 1) {
    out[i] = a[i] ^ b[i];
  }
  return out;
}

function xorComponents(parts) {
  let merged = parts[0];
  for (let i = 1; i < parts.length; i += 1) {
    merged = xor(merged, parts[i]);
  }
  return Buffer.from(merged);
}

function getWorkKey(materialDir) {
  const root = path.resolve(materialDir, "material");
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
    throw new Error(`缺少 material 目录: ${root}`);
  }

  const fdRoot = path.join(root, DIRS[0]);
  const fdParts = fs
    .readdirSync(fdRoot)
    .filter((name) => name !== MAC_DS_STORE)
    .sort()
    .map((name) => readDirBytes(path.join(fdRoot, name)));

  if (fdParts.length !== 3) {
    throw new Error(`${fdRoot} 结构不正确`);
  }

  const salt = readDirBytes(path.join(root, DIRS[1]));
  const workMaterial = readDirBytes(path.join(root, DIRS[2]));
  const merged = xorComponents([...fdParts, COMPONENT]);
  const rootKey = crypto.pbkdf2Sync(merged.toString(), Buffer.from(salt), 10000, 16, "sha256");
  return { key: new Int8Array(rootKey), workMaterial };
}

function decryptAesGcm(key, payload) {
  const contentLen =
    ((payload[0] & 255) << 24) |
    ((payload[1] & 255) << 16) |
    ((payload[2] & 255) << 8) |
    (payload[3] & 255);
  const ivLen = payload.length - 4 - contentLen;
  const iv = payload.slice(4, 4 + ivLen);
  const tag = payload.slice(payload.length - 16);
  const decipher = crypto.createDecipheriv("aes-128-gcm", Buffer.from(key), Buffer.from(iv));
  decipher.setAuthTag(Buffer.from(tag));
  const plain = Buffer.concat([
    decipher.update(payload.subarray(4 + ivLen, payload.length - 16)),
    decipher.final(),
  ]);
  return plain;
}

function encryptAesGcm(key, plaintext) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-128-gcm", Buffer.from(key), iv);
  const encrypted = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  const contentLen = encrypted.length + tag.length;
  const out = Buffer.alloc(4 + iv.length + encrypted.length + tag.length);
  out[0] = (contentLen >> 24) & 255;
  out[1] = (contentLen >> 16) & 255;
  out[2] = (contentLen >> 8) & 255;
  out[3] = contentLen & 255;
  iv.copy(out, 4);
  encrypted.copy(out, 16);
  tag.copy(out, 16 + encrypted.length);
  return out;
}

function getKey(materialDir) {
  const { key, workMaterial } = getWorkKey(materialDir);
  return new Int8Array(decryptAesGcm(key, workMaterial));
}

function encryptPassword(materialDir, plaintext) {
  const text = String(plaintext || "").trim();
  if (!text) {
    throw new Error("密码不能为空");
  }
  if (/^000000[0-9a-fA-F]+$/.test(text) && text.length >= 32) {
    return text;
  }
  const key = getKey(materialDir);
  return encryptAesGcm(key, text).toString("hex");
}

function decryptPassword(materialDir, encryptedHex) {
  const key = getKey(materialDir);
  const payload = new Int8Array(Buffer.from(String(encryptedHex).trim(), "hex"));
  return decryptAesGcm(key, payload).toString("utf8");
}

function main() {
  const [, , action, materialDir, value] = process.argv;
  if (!action || !materialDir || value === undefined) {
    console.error("usage: node harmony_signing_password.js <encrypt|decrypt> <materialDir> <value>");
    process.exit(2);
  }
  try {
    const out =
      action === "encrypt"
        ? encryptPassword(materialDir, value)
        : action === "decrypt"
          ? decryptPassword(materialDir, value)
          : null;
    if (out === null) {
      throw new Error(`未知 action: ${action}`);
    }
    process.stdout.write(String(out));
  } catch (err) {
    console.error(err.message || String(err));
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}

module.exports = {
  encryptPassword,
  decryptPassword,
};

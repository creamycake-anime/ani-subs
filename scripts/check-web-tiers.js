#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");

const rootDir = path.resolve(__dirname, "..");
const webDir = path.join(rootDir, "subs", "web");

function listJsonFiles(dir) {
  const files = [];

  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...listJsonFiles(fullPath));
    } else if (entry.isFile() && entry.name.endsWith(".json")) {
      files.push(fullPath);
    }
  }

  return files.sort((a, b) => a.localeCompare(b));
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    throw new Error(`${path.relative(rootDir, file)}: ${error.message}`);
  }
}

const errors = [];
let checked = 0;

for (const file of listJsonFiles(webDir)) {
  checked += 1;

  const relativePath = path.relative(webDir, file);
  const tierDir = relativePath.split(path.sep)[0];
  const match = /^t([0-4])$/.exec(tierDir);
  const relativeFile = path.relative(rootDir, file);

  if (!match) {
    errors.push(`${relativeFile}: expected file to be under subs/web/t0 through subs/web/t4`);
    continue;
  }

  const expectedTier = Number(match[1]);
  const source = readJson(file);
  const actualTier = source?.arguments?.tier;

  if (!Number.isInteger(actualTier)) {
    errors.push(`${relativeFile}: expected arguments.tier to be an integer`);
    continue;
  }

  if (actualTier !== expectedTier) {
    errors.push(`${relativeFile}: arguments.tier is ${actualTier}, expected ${expectedTier}`);
  }
}

if (errors.length > 0) {
  for (const error of errors) {
    console.error(error);
  }
  process.exitCode = 1;
} else {
  console.log(`checked ${checked} web source tier(s)`);
}

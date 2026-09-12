// Collect notices only for package files that contribute bytes to the bundle.
import fs from 'node:fs';
import path from 'node:path';

export function collectFrontendLicenses(metafile, root) {
  const packages = new Map();
  for (const output of Object.values(metafile.outputs)) {
    for (const [input, contribution] of Object.entries(output.inputs)) {
      if (contribution.bytesInOutput === 0) continue;
      const absolute = path.resolve(root, input);
      const segments = absolute.split(path.sep);
      const index = segments.lastIndexOf('node_modules');
      if (index < 0) continue;
      const scoped = segments[index + 1]?.startsWith('@');
      const packageRoot = segments.slice(0, index + (scoped ? 3 : 2)).join(path.sep);
      if (packages.has(packageRoot)) continue;
      // Read the package at the input's own location, including nested versions.
      // A missing manifest or license is a build failure, never an omitted row.
      const manifest = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
      if (!manifest.name || !manifest.version) throw new Error(`Missing package identity: ${packageRoot}`);
      const names = fs.readdirSync(packageRoot).filter(name =>
        /^(licen[cs]e|copying|copyright|notice)(?:$|[._-])/i.test(name)
        && fs.statSync(path.join(packageRoot, name)).isFile()).sort();
      if (!names.some(name => /^(licen[cs]e|copying)(?:$|[._-])/i.test(name))) {
        throw new Error(`Missing license text for ${manifest.name}@${manifest.version}: ${packageRoot}`);
      }
      const notices = names.map(name => {
        const text = fs.readFileSync(path.join(packageRoot, name), 'utf8').replace(/\r\n/g, '\n').trimEnd();
        if (!text.trim()) throw new Error(`Empty notice: ${packageRoot}/${name}`);
        return { name, text };
      });
      packages.set(packageRoot, { name: manifest.name, version: manifest.version,
        license: typeof manifest.license === 'string' ? manifest.license : 'not specified', notices });
    }
  }
  // Identical copies collapse; distinct versions or notice texts remain separate.
  const unique = [...new Map([...packages.values()].map(row => [JSON.stringify(row), row])).values()];
  return unique.sort((a, b) => {
    const left = JSON.stringify(a), right = JSON.stringify(b);
    return left < right ? -1 : left > right ? 1 : 0;
  });
}

export function renderFrontendLicenses(rows) {
  const parts = [
    '# 前端产物内联的第三方代码许可',
    '# 由 npm run build / scripts/build_frontend_licenses.mjs 生成，请勿手工编辑。',
    '# 范围：esbuild metafile 中实际贡献产物字节的 node_modules 包。',
    '# 各包许可标识来自其 package.json；以下保留许可和声明文件原文。',
    `# 共 ${rows.length} 个包版本及声明组合。`, '',
  ];
  for (const row of rows) {
    parts.push('='.repeat(72), `=== ${row.name} ${row.version} (${row.license})`, '='.repeat(72));
    for (const notice of row.notices) parts.push(`--- ${notice.name} ---`, notice.text, '');
  }
  return parts.join('\n');
}

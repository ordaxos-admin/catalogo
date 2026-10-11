import fs from 'node:fs'
import path from 'node:path'

const [rootArg = 'dist', expectation = 'absent'] = process.argv.slice(2)
if (!['absent', 'present'].includes(expectation)) {
  throw new Error(`expectation must be absent or present; found ${expectation}`)
}

const root = path.resolve(rootArg)
if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
  throw new Error(`artifact directory not found: ${root}`)
}

const markers = [
  'Sentinela Rubra',
  'Crônicas de Ferro',
  'mdl-000001',
]

function filesUnder(directory) {
  const result = []
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name)
    if (entry.isDirectory()) result.push(...filesUnder(target))
    else if (entry.isFile()) result.push(target)
  }
  return result
}

const files = filesUnder(root)
const hits = new Map(markers.map((marker) => [marker, []]))

for (const file of files) {
  const buffer = fs.readFileSync(file)
  if (buffer.includes(0)) continue
  const text = buffer.toString('utf8')
  for (const marker of markers) {
    if (text.includes(marker)) hits.get(marker).push(path.relative(root, file))
  }
}

if (expectation === 'absent') {
  const publicSourceMaps = files
    .map((file) => path.relative(root, file))
    .filter((file) => file.endsWith('.map'))
  if (publicSourceMaps.length) {
    throw new Error(`Production artifact must not publish source maps: ${publicSourceMaps.join(', ')}`)
  }

  const leaked = [...hits].filter(([, locations]) => locations.length)
  if (leaked.length) {
    const detail = leaked.map(([marker, locations]) => `${marker}: ${locations.join(', ')}`).join('; ')
    throw new Error(`Production artifact contains demo-only catalog data: ${detail}`)
  }
  console.log(`Production artifact has no public source maps and is free of ${markers.length} demo-only sentinels across ${files.length} files`)
} else {
  const missing = [...hits].filter(([, locations]) => !locations.length).map(([marker]) => marker)
  if (missing.length) throw new Error(`Explicit demo artifact is missing demo sentinels: ${missing.join(', ')}`)
  console.log(`Explicit demo artifact contains all ${markers.length} demo-only sentinels`)
}

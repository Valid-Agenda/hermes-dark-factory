const fs = require('fs')
const vm = require('vm')

const path = process.argv[2] || 'plugin/desktop/plugin.js'
const source = fs.readFileSync(path, 'utf8')
  .replace(/^import[\s\S]*?from 'react\/jsx-runtime'\n/, '')
  .replace('export default {', 'const plugin = {')
  + '\nglobalThis.__plugin = plugin\n'

const registrations = []
const jsx = (type, props = {}) => ({ type, props })
const jsxs = jsx
const context = {
  console,
  Object,
  Array,
  Boolean,
  Number,
  String,
  Math,
  Date,
  JSON,
  Promise,
  URLSearchParams,
  encodeURIComponent,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  React: {},
  Button: 'Button',
  Input: 'Input',
  Textarea: 'Textarea',
  PALETTE_AREA: 'palette',
  ROUTES_AREA: 'routes',
  SIDEBAR_NAV_AREA: 'sidebar',
  host: {
    navigate: () => {},
    notify: () => {},
    request: async () => ({})
  },
  useCallback: (fn) => fn,
  useEffect: () => {},
  useMemo: (fn) => fn(),
  useState: initial => {
    const value = typeof initial === 'function' ? initial() : initial
    return [value === true ? false : value, () => {}]
  },
  jsx,
  jsxs,
  Field: 'Field',
  Section: 'Section',
  ReadinessPanel: 'ReadinessPanel',
  ModelAssignment: 'ModelAssignment'
}
vm.createContext(context)
vm.runInContext(source, context, { filename: path })
const plugin = context.__plugin
plugin.register({
  rest: async () => ({ projects: [], config: {}, model_options: {}, readiness: {}, setup: {} }),
  registerMany: items => registrations.push(...items)
})
const routes = registrations.filter(item => item.area === 'routes').map(item => item.data?.path)
const sidebar = registrations.find(item => item.area === 'sidebar')
const expected = ['/dark-factory', '/dark-factory/project', '/dark-factory/settings', '/dark-factory/setup']
for (const route of expected) if (!routes.includes(route)) throw new Error(`missing route ${route}`)
if (!sidebar || sidebar.data?.path !== '/dark-factory') throw new Error('missing Dark Factory sidebar contribution')
const palettes = registrations.filter(item => item.area === 'palette')
if (palettes.length !== 2 || !palettes.some(item => item.data?.id === 'dark-factory.open') || !palettes.some(item => item.data?.id === 'dark-factory.settings')) {
  throw new Error('missing Dark Factory palette contributions')
}
for (const item of palettes) if (typeof item.data?.run !== 'function') throw new Error(`palette ${item.data?.id} is not executable`)
const projectRoute = registrations.find(item => item.area === 'routes' && item.data?.path === '/dark-factory')
const virtual = projectRoute.render()
const rendered = virtual.type(virtual.props)
const visibleText = node => {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  const children = node.props?.children
  return Array.isArray(children) ? children.map(visibleText).join(' ') : visibleText(children)
}
const emptyText = visibleText(rendered)
for (const label of ['No Hermes projects yet', 'Global defaults', 'New project']) {
  if (!emptyText.includes(label)) throw new Error(`empty Projects view missing ${label}`)
}
console.log(JSON.stringify({
  plugin: plugin.id,
  registration_count: registrations.length,
  routes,
  sidebar: sidebar.data.label,
  palette: palettes.map(item => item.data.id),
  empty_state: 'rendered'
}))

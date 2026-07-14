import assert from 'node:assert/strict'
import test from 'node:test'

import {
  normalizeUIManifestResponse,
  shellPages,
  visibleNavigation,
  visibleWidgets,
} from './UIManifestTools.js'

test('manifest fallback is complete and theme neutral', () => {
  const manifest = normalizeUIManifestResponse(null)
  assert.equal(manifest.title, '世界模拟器')
  assert.equal(manifest.terminology.player, '玩家')
  assert.deepEqual(visibleNavigation(manifest, 'main').map(item => item.view), ['dashboard', 'world', 'relations', 'debug'])
  assert.equal(visibleWidgets(manifest, 'top')[0].kind, 'objectives')
  assert.equal(String(manifest).includes('朝堂'), false)
})

test('normalizer accepts allowlisted capabilities and drops injected ones', () => {
  const manifest = normalizeUIManifestResponse({
    configured: true,
    generation: 5,
    manifest: {
      title: '<script>bad</script>',
      terminology: { player: '指挥官' },
      navigation: [
        { nav_id: 'command', surface: 'main', view: 'dashboard', label: '指挥台', icon: '台', order: 20 },
        { nav_id: 'evil', surface: 'main', view: 'remote_component', label: 'Bad', icon: 'X', href: 'https://bad.test' },
        { nav_id: 'studio', surface: 'shell', view: 'dlc_studio', label: '内容包', icon: '包' },
      ],
      dashboard: { layout: 'single_column', widgets: [
        { widget_id: 'message', kind: 'player_message', region: 'main', order: 2 },
        { widget_id: 'evil', kind: 'html', region: 'main', component: 'Remote' },
      ]},
      issue_page: { list_fields: ['title', 'raw_html', 'title'], detail_fields: ['summary'] },
      entity_detail: { visible_fields: ['name', 'secret'], sections: ['summary', 'iframe'] },
    },
  })

  assert.equal(manifest.title, '世界模拟器')
  assert.equal(manifest.terminology.player, '指挥官')
  assert.deepEqual(visibleNavigation(manifest, 'main').map(item => item.view), ['dashboard'])
  assert.deepEqual(visibleWidgets(manifest, 'main').map(item => item.kind), ['player_message'])
  assert.deepEqual(manifest.issue_page.list_fields, ['title'])
  assert.deepEqual(manifest.entity_detail.visible_fields, ['name'])
  assert.deepEqual(manifest.entity_detail.sections, ['summary'])
  assert.equal(shellPages(manifest, '/dlc.html')[0].href, '/dlc.html')
  assert.equal(shellPages(manifest, '/dlc.html')[0].active, true)
})

test('missing visible main and shell navigation gets minimal safe recovery routes', () => {
  const manifest = normalizeUIManifestResponse({ manifest: { navigation: [] } })
  assert.deepEqual(visibleNavigation(manifest, 'main').map(item => item.view), ['dashboard'])
  assert.deepEqual(visibleNavigation(manifest, 'shell').map(item => item.view), ['gameplay'])
})

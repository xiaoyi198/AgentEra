export const LEGACY_RELATIONSHIP_TYPES = [
  {
    relationship_type_id: 'subordinate',
    display_name: '上下级',
    description: '有方向的管理、指挥或隶属关系。',
    direction: 'directed',
    minimum_strength: 0,
    maximum_strength: 100,
    default_strength: 100,
    default_visibility: 'public',
    allow_expiry: false,
    dimensions: [],
    style: { label: '属', color: '#c9a84c', dashed: false, marker: 'closed' },
    metadata: {},
    legacy: true,
  },
  {
    relationship_type_id: 'affiliated',
    display_name: '附属',
    description: '有方向的归属、挂靠或附属关系。',
    direction: 'directed',
    minimum_strength: 0,
    maximum_strength: 100,
    default_strength: 50,
    default_visibility: 'public',
    allow_expiry: false,
    dimensions: [],
    style: { label: '附', color: '#4a6fa5', dashed: true, marker: 'arrow' },
    metadata: {},
    legacy: true,
  },
]

export function effectiveRelationshipTypes(content) {
  const authored = Array.isArray(content?.relationship_types) ? content.relationship_types : []
  const byId = new Map(LEGACY_RELATIONSHIP_TYPES.map(item => [item.relationship_type_id, item]))
  for (const item of authored) {
    const id = String(item?.relationship_type_id || item?.id || '').trim()
    if (id) byId.set(id, { ...item, relationship_type_id: id, legacy: false })
  }
  return [...byId.values()]
}

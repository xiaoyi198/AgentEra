import { useEffect, useRef, useState } from 'react'
import './AppNav.css'

import { normalizeUIManifestResponse, shellPages } from './UIManifestTools.js'

const STORAGE_KEY = 'appNav.position.v2'
const MARGIN = 8
const DRAG_THRESHOLD = 4

function loadPosition() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (typeof parsed?.x === 'number' && typeof parsed?.y === 'number') return parsed
  } catch { /* localStorage may be unavailable */ }
  return null
}

function clampToViewport(x, y, w, h) {
  const maxX = Math.max(MARGIN, window.innerWidth - w - MARGIN)
  const maxY = Math.max(MARGIN, window.innerHeight - h - MARGIN)
  return {
    x: Math.min(Math.max(MARGIN, x), maxX),
    y: Math.min(Math.max(MARGIN, y), maxY),
  }
}

export default function AppNav() {
  const pathname = typeof window !== 'undefined' ? window.location.pathname : '/'
  const navRef = useRef(null)
  const dragStateRef = useRef(null)
  const [position, setPosition] = useState(() => loadPosition())
  const [dragging, setDragging] = useState(false)
  const [uiManifest, setUIManifest] = useState(() => normalizeUIManifestResponse(null))
  const suppressClickRef = useRef(false)

  useEffect(() => {
    let active = true
    fetch('/api/ui-manifest')
      .then(response => response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`)))
      .then(data => { if (active) setUIManifest(normalizeUIManifestResponse(data)) })
      .catch(() => {})
    return () => { active = false }
  }, [])

  // 初始化默认位置（底部居中），并在窗口尺寸改变后保持在视口内
  useEffect(() => {
    const applyDefault = () => {
      const el = navRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      setPosition((prev) => {
        if (prev) return clampToViewport(prev.x, prev.y, rect.width, rect.height)
        return { x: Math.max(MARGIN, (window.innerWidth - rect.width) / 2), y: window.innerHeight - rect.height - 18 }
      })
    }
    applyDefault()
    window.addEventListener('resize', applyDefault)
    return () => window.removeEventListener('resize', applyDefault)
  }, [])

  // 持久化
  useEffect(() => {
    if (!position) return
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(position))
    } catch { /* localStorage may be unavailable */ }
  }, [position])

  useEffect(() => {
    const onMove = (e) => {
      const s = dragStateRef.current
      if (!s) return
      const dx = e.clientX - s.startX
      const dy = e.clientY - s.startY
      if (!s.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return
      if (!s.moved) {
        s.moved = true
        setDragging(true)
      }
      e.preventDefault()
      setPosition(
        clampToViewport(e.clientX - s.offsetX, e.clientY - s.offsetY, s.width, s.height)
      )
    }
    const onUp = () => {
      const s = dragStateRef.current
      if (!s) return
      if (s.moved) {
        suppressClickRef.current = true
        setTimeout(() => { suppressClickRef.current = false }, 0)
      }
      dragStateRef.current = null
      setDragging(false)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [])

  const onPointerDown = (e) => {
    if (e.button !== 0) return
    const el = navRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    dragStateRef.current = {
      offsetX: e.clientX - rect.left,
      offsetY: e.clientY - rect.top,
      width: rect.width,
      height: rect.height,
      startX: e.clientX,
      startY: e.clientY,
      moved: false,
    }
  }

  const onLinkClick = (e) => {
    if (suppressClickRef.current) {
      e.preventDefault()
      e.stopPropagation()
    }
  }

  const style = position
    ? { left: `${position.x}px`, top: `${position.y}px`, right: 'auto' }
    : { visibility: 'hidden' }

  return (
    <nav
      ref={navRef}
      className={`app-nav${dragging ? ' app-nav-dragging' : ''}`}
      style={style}
      aria-label="页面导航"
      onPointerDown={onPointerDown}
    >
      <span className="app-nav-handle" title="拖动以移动" aria-hidden="true">⋮⋮</span>
      {shellPages(uiManifest, pathname).map((page) => {
        return (
          <a
            key={page.nav_id}
            href={page.href}
            className={page.active ? 'app-nav-active' : ''}
            aria-current={page.active ? 'page' : undefined}
            onClick={onLinkClick}
            draggable={false}
          >
            <span className="app-nav-icon" aria-hidden="true">{page.icon}</span>
            <span>{page.label}</span>
          </a>
        )
      })}
    </nav>
  )
}

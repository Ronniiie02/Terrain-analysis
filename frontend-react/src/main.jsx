import React from 'react'
import { createRoot } from 'react-dom/client'

// 不渲染任何可见 DOM，防止影响你原有结构
const mount = document.createElement('div')
mount.id = 'react-root-placeholder'
mount.style.display = 'none'
document.body.appendChild(mount)

createRoot(mount).render(<></>)

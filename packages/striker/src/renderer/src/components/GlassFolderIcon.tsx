// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useId } from 'react'

export interface GlassFolderIconProps {
  size?: number
  color?: string
  badgeIcon?: 'dossier' | 'finance' | 'wellness' | 'projects' | 'social' | 'academic' | 'cybersec' | 'ops' | 'folder'
  glow?: boolean
}

export default function GlassFolderIcon({
  size = 56,
  color = '#5fcce6',
  badgeIcon = 'folder',
  glow = true,
}: GlassFolderIconProps) {
  const rawId = useId()
  const id = rawId.replace(/[^a-zA-Z0-9]/g, '')

  const renderBadge = () => {
    switch (badgeIcon) {
      case 'dossier':
        return (
          <path
            d="M32 30a5 5 0 1 0 0-10 5 5 0 0 0 0 10zm0 3c-4.5 0-9 2.3-9 5.5v1.5h18v-1.5c0-3.2-4.5-5.5-9-5.5z"
            fill="currentColor"
            opacity="0.95"
          />
        )
      case 'finance':
        return (
          <path
            d="M32 20v24m-6-18h10a4 4 0 0 1 0 8H26a4 4 0 0 0 0 8h12"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )
      case 'wellness':
        return (
          <path
            d="M22 32h5l3-7 4 14 3-7h5"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )
      case 'projects':
        return (
          <path
            d="M26 23l6-3 6 3v8l-6 3-6-3v-8zm6-3v14m6-6l-6-3-6 3"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )
      case 'social':
        return (
          <path
            d="M28 28a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm8 2a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm-8 2c-3.8 0-7 2-7 4.5v1.5h14v-1.5c0-2.5-3.2-4.5-7-4.5zm8 1c-.7 0-1.4.1-2 .3.9 1 1.5 2.1 1.5 3.2v1.5h6v-1.5c0-2-2.5-3.5-5.5-3.5z"
            fill="currentColor"
            opacity="0.95"
          />
        )
      case 'academic':
        return (
          <path
            d="M22 25l10-5 10 5-10 5-10-5zm16 6v7c-2 2-10 2-12 0v-7"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )
      case 'cybersec':
        return (
          <path
            d="M32 20l9 4v6c0 6-4 11-9 14-5-3-9-8-9-14v-6l9-4z"
            stroke="currentColor"
            strokeWidth="2.2"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )
      case 'ops':
        return (
          <path
            d="M24 23h16v6H24v-6zm0 10h16v6H24v-6zm4-7h2m-2 10h2"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )
      default:
        return (
          <path
            d="M28 28h8m-4-4v8"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
          />
        )
    }
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{
        filter: glow ? `drop-shadow(0 4px 14px ${color}33)` : 'none',
        transition: 'transform 0.25s cubic-bezier(0.2, 0.9, 0.3, 1), filter 0.25s ease',
        flexShrink: 0,
      }}
    >
      <defs>
        {/* Backplate glass gradient */}
        <linearGradient id={`back-grad-${id}`} x1="8" y1="10" x2="56" y2="54" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="rgba(255, 255, 255, 0.22)" />
          <stop offset="100%" stopColor="rgba(12, 16, 24, 0.75)" />
        </linearGradient>

        {/* Front flap frosted liquid gradient */}
        <linearGradient id={`front-grad-${id}`} x1="6" y1="22" x2="58" y2="56" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor={color} stopOpacity="0.32" />
          <stop offset="35%" stopColor="rgba(255, 255, 255, 0.16)" />
          <stop offset="100%" stopColor="rgba(10, 14, 20, 0.85)" />
        </linearGradient>

        {/* Specular light streak across front glass */}
        <linearGradient id={`sheen-grad-${id}`} x1="8" y1="22" x2="48" y2="46" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="rgba(255, 255, 255, 0.55)" />
          <stop offset="25%" stopColor="rgba(255, 255, 255, 0.15)" />
          <stop offset="100%" stopColor="rgba(255, 255, 255, 0)" />
        </linearGradient>

        {/* Tab glow gradient */}
        <linearGradient id={`tab-grad-${id}`} x1="8" y1="12" x2="28" y2="18" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor={color} stopOpacity="0.75" />
          <stop offset="100%" stopColor={color} stopOpacity="0.25" />
        </linearGradient>
      </defs>

      {/* ── Layer 1: Backplate (Folder back & tab) ─────────────────────────── */}
      <path
        d="M8 17c0-2.2 1.8-4 4-4h11.2c1.6 0 3 .9 3.7 2.3l1.8 3.7h23.3c2.2 0 4 1.8 4 4v23c0 2.2-1.8 4-4 4H12c-2.2 0-4-1.8-4-4V17z"
        fill={`url(#back-grad-${id})`}
        stroke="rgba(255, 255, 255, 0.18)"
        strokeWidth="1.2"
      />

      {/* Folder Tab Neon Accent Stripe */}
      <path
        d="M10 16c0-1.1.9-2 2-2h9.5l2.5 5H10v-3z"
        fill={`url(#tab-grad-${id})`}
      />

      {/* ── Layer 2: Inner Document Sheet (peek) ──────────────────────────── */}
      <rect
        x="13"
        y="18"
        width="38"
        height="28"
        rx="3"
        fill="rgba(255, 255, 255, 0.08)"
        stroke="rgba(255, 255, 255, 0.15)"
        strokeWidth="1"
      />
      {/* Document line hint */}
      <line x1="18" y1="23" x2="32" y2="23" stroke="rgba(255, 255, 255, 0.3)" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="18" y1="27" x2="42" y2="27" stroke="rgba(255, 255, 255, 0.2)" strokeWidth="1.5" strokeLinecap="round" />

      {/* ── Layer 3: Front Frosted Glass Plate ────────────────────────────── */}
      <path
        d="M6 26c0-2.2 1.8-4 4-4h44c2.2 0 4 1.8 4 4l-1.8 24c-.1 2.2-1.9 4-4.1 4H11.9c-2.2 0-4-1.8-4.1-4L6 26z"
        fill={`url(#front-grad-${id})`}
        stroke="rgba(255, 255, 255, 0.28)"
        strokeWidth="1.2"
      />

      {/* Specular glass reflection diagonal streak */}
      <path
        d="M8 24h42L22 54H9L8 24z"
        fill={`url(#sheen-grad-${id})`}
        opacity="0.6"
      />

      {/* Top rim catch line (Stark precision edge) */}
      <path
        d="M8 23h48"
        stroke="rgba(255, 255, 255, 0.65)"
        strokeWidth="1"
        strokeLinecap="round"
      />

      {/* Bottom rim specular catch */}
      <path
        d="M12 53h40"
        stroke={color}
        strokeOpacity="0.4"
        strokeWidth="1"
        strokeLinecap="round"
      />

      {/* ── Layer 4: Holographic Center Emblem ────────────────────────────── */}
      <g color={color} transform="translate(0, 4)">
        {renderBadge()}
      </g>
    </svg>
  )
}

"""Build theme-palette-reference.canvas.tsx with embedded theme data."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rows = json.loads((ROOT / "desktop" / "theme_palette_reference.json").read_text(encoding="utf-8"))
data = json.dumps(rows, separators=(",", ":"))

canvas = Path(
    r"C:\Users\win10\.cursor\projects\c-Users-win10-Downloads-mac2-2-mac2\canvases"
    r"\theme-palette-reference.canvas.tsx"
)

# Split so we don't fight quotes: header / data / footer
header = r'''import { useMemo, useState } from 'react'
import {
  Card,
  CardBody,
  CardHeader,
  Code,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Pill,
  Row,
  Select,
  Spacer,
  Stack,
  Stat,
  Table,
  Text,
  useCanvasState,
} from 'cursor/canvas'

type ThemeRow = {
  id: string
  type: 'dark' | 'light'
  family: string
  palette: Record<string, string>
  ui: Record<string, string>
}

const THEMES: ThemeRow[] = '''

footer = r'''

const ROLE_GROUPS: {
  title: string
  keys: { key: string; label: string; tip: string }[]
}[] = [
  {
    title: 'Surfaces / layers',
    keys: [
      { key: 'window_bg', label: 'Window background', tip: 'Page chrome (--bg)' },
      {
        key: 'panel_surface',
        label: 'Panel / table / input surface',
        tip: '--surface ← inputbg',
      },
      {
        key: 'surface_alt_hover',
        label: 'Alt / hover / zebra tint',
        tip: '--surface-2 ← active',
      },
      {
        key: 'soft_sunken',
        label: 'Soft / sunken / readonly',
        tip: 'light themes: light; dark: active',
      },
      { key: 'border', label: 'Border', tip: '--border' },
    ],
  },
  {
    title: 'Text',
    keys: [
      { key: 'text_primary', label: 'Primary text', tip: 'Body / headings (--fg)' },
      {
        key: 'text_secondary_muted',
        label: 'Secondary / muted text',
        tip: 'Labels, hints (--fg-muted)',
      },
      {
        key: 'text_hint_placeholder',
        label: 'Hint / placeholder',
        tip: '--fg-hint',
      },
      {
        key: 'accent_text',
        label: 'Accent text',
        tip: 'Links / accent labels (--accent-text)',
      },
      {
        key: 'label_accent',
        label: 'Field label accent',
        tip: 'Light: secondary; dark: accent-text',
      },
    ],
  },
  {
    title: 'Buttons',
    keys: [
      {
        key: 'btn_primary_bg',
        label: 'Primary button background',
        tip: 'primary + white label',
      },
      { key: 'btn_primary_fg', label: 'Primary button text', tip: 'Always white' },
      {
        key: 'btn_secondary_bg',
        label: 'Secondary button background',
        tip: 'secondary + white label',
      },
      {
        key: 'btn_secondary_fg',
        label: 'Secondary button text',
        tip: 'Always white',
      },
      {
        key: 'btn_neutral_bg',
        label: 'Neutral button background',
        tip: 'Surface + border',
      },
      { key: 'btn_neutral_fg', label: 'Neutral button text', tip: 'Primary text' },
      {
        key: 'btn_neutral_border',
        label: 'Neutral button border',
        tip: 'Border token',
      },
    ],
  },
  {
    title: 'Inputs & selection',
    keys: [
      { key: 'input_bg', label: 'Input background', tip: 'inputbg' },
      { key: 'input_fg', label: 'Input text', tip: 'inputfg' },
      { key: 'selection_bg', label: 'Selection background', tip: 'selectbg' },
      { key: 'selection_fg', label: 'Selection text', tip: 'selectfg' },
    ],
  },
  {
    title: 'Status / semantic',
    keys: [
      {
        key: 'status_success',
        label: 'Success',
        tip: 'Dark: palette; light: alert_colors',
      },
      { key: 'status_info', label: 'Info', tip: 'Status / QA info' },
      { key: 'status_warning', label: 'Warning', tip: 'Status / QA warning' },
      { key: 'status_danger', label: 'Danger', tip: 'Status / QA danger' },
    ],
  },
]

const PALETTE_KEYS = [
  ['primary', 'Primary'],
  ['secondary', 'Secondary'],
  ['bg', 'bg'],
  ['fg', 'fg'],
  ['inputbg', 'inputbg'],
  ['inputfg', 'inputfg'],
  ['active', 'active'],
  ['light', 'light'],
  ['dark', 'dark'],
  ['border', 'border'],
  ['selectbg', 'selectbg'],
  ['selectfg', 'selectfg'],
  ['success', 'success'],
  ['info', 'info'],
  ['warning', 'warning'],
  ['danger', 'danger'],
] as const

function ColorCell({ hex }: { hex: string }) {
  if (!hex) return <Text tone="secondary">—</Text>
  return (
    <Row gap={8} align="center">
      <span
        style={{
          width: 14,
          height: 14,
          borderRadius: 3,
          background: hex,
          flexShrink: 0,
          border: '1px solid rgba(128,128,128,0.35)',
        }}
      />
      <Code>{hex}</Code>
    </Row>
  )
}

export default function ThemePaletteReference() {
  const [themeId, setThemeId] = useCanvasState('themeId', 'steel-dark')
  const [mode, setMode] = useState<'ui' | 'palette'>('ui')

  const theme = useMemo(
    () => THEMES.find((t) => t.id === themeId) || THEMES[0],
    [themeId],
  )

  const options = THEMES.map((t) => ({
    value: t.id,
    label: `${t.id} (${t.type})`,
  }))

  const uiRows = ROLE_GROUPS.flatMap((g) =>
    g.keys.map((k) => ({
      group: g.title,
      role: k.label,
      tip: k.tip,
      hex: theme.ui[k.key] || '',
    })),
  )

  const paletteRows = PALETTE_KEYS.map(([key, label]) => ({
    key: label,
    hex: theme.palette[key] || '',
  }))

  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Satpuda theme color reference</H1>
        <Text tone="secondary">
          All 20 themes — raw CUSTOM_THEMES palette plus desktop UI layer tokens
          (text, buttons, surfaces). Source: core/custom_themes.py + generated
          CSS mapping.
        </Text>
      </Stack>

      <Row gap={12} align="center" wrap>
        <Select
          value={theme.id}
          onChange={setThemeId}
          options={options}
          placeholder="Theme"
        />
        <Pill tone={theme.type === 'dark' ? 'neutral' : 'info'}>{theme.type}</Pill>
        <Pill tone="accent">{theme.family}</Pill>
        <Row gap={8}>
          <Stat value={String(THEMES.length)} label="Themes" />
          <Stat value="10" label="Dark" />
          <Stat value="10" label="Light" />
        </Row>
      </Row>

      <Grid columns={4} gap={10}>
        {(
          [
            ['Primary', theme.ui.btn_primary_bg],
            ['Secondary btn', theme.ui.btn_secondary_bg],
            ['Window', theme.ui.window_bg],
            ['Text', theme.ui.text_primary],
            ['Muted text', theme.ui.text_secondary_muted],
            ['Panel', theme.ui.panel_surface],
            ['Border', theme.ui.border],
            ['Accent text', theme.ui.accent_text],
          ] as const
        ).map(([label, hex]) => (
          <Card key={label} size="sm">
            <CardHeader trailing={<Code>{hex}</Code>}>{label}</CardHeader>
            <CardBody>
              <div
                style={{
                  height: 36,
                  borderRadius: 6,
                  background: hex,
                  border: '1px solid rgba(128,128,128,0.25)',
                }}
              />
            </CardBody>
          </Card>
        ))}
      </Grid>

      <Row gap={8}>
        <Pill
          active={mode === 'ui'}
          tone={mode === 'ui' ? 'accent' : 'neutral'}
          onClick={() => setMode('ui')}
        >
          UI roles
        </Pill>
        <Pill
          active={mode === 'palette'}
          tone={mode === 'palette' ? 'accent' : 'neutral'}
          onClick={() => setMode('palette')}
        >
          Raw palette keys
        </Pill>
      </Row>

      {mode === 'ui' ? (
        <Card>
          <CardHeader>UI color roles — {theme.id}</CardHeader>
          <CardBody>
            <Table
              headers={['Layer', 'Role', 'Hex', 'Maps to']}
              rows={uiRows.map((r) => [
                r.group,
                r.role,
                <ColorCell key={r.role} hex={r.hex} />,
                r.tip,
              ])}
            />
          </CardBody>
        </Card>
      ) : (
        <Card>
          <CardHeader>CUSTOM_THEMES palette — {theme.id}</CardHeader>
          <CardBody>
            <Table
              headers={['Key', 'Hex']}
              rows={paletteRows.map((r) => [
                r.key,
                <ColorCell key={r.key} hex={r.hex} />,
              ])}
            />
          </CardBody>
        </Card>
      )}

      <Divider />
      <H2>All themes — primary / secondary / text snapshot</H2>
      <Text tone="secondary">
        Quick scan across every theme. Select a theme above for full layers.
      </Text>
      <Table
        headers={[
          'Theme',
          'Type',
          'Primary',
          'Secondary',
          'Text',
          'Muted text',
          'Window',
          'Panel',
        ]}
        rows={THEMES.map((t) => [
          t.id,
          t.type,
          <ColorCell key={t.id + 'p'} hex={t.ui.btn_primary_bg} />,
          <ColorCell key={t.id + 's'} hex={t.ui.btn_secondary_bg} />,
          <ColorCell key={t.id + 't'} hex={t.ui.text_primary} />,
          <ColorCell key={t.id + 'm'} hex={t.ui.text_secondary_muted} />,
          <ColorCell key={t.id + 'w'} hex={t.ui.window_bg} />,
          <ColorCell key={t.id + 'pan'} hex={t.ui.panel_surface} />,
        ])}
      />

      <Spacer height={8} />
      <H3>Notes</H3>
      <Stack gap={4}>
        <Text>
          Secondary is a solid button fill with white text — not muted label
          color (except light themes use secondary as field-label accent).
        </Text>
        <Text>
          Dark muted text is blended from fg→bg for contrast; light muted uses
          classic alert_colors muted map.
        </Text>
        <Text tone="secondary">
          Also saved as desktop/theme_palette_reference.json in the repo.
        </Text>
      </Stack>
    </Stack>
  )
}
'''

canvas.write_text(header + data + footer, encoding="utf-8")
print(f"Wrote {canvas} ({canvas.stat().st_size} bytes)")

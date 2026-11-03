"""Small client-only contract migration, preserving layout and interaction style."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def replace(text, old, new):
    if old not in text:
        if new in text:
            return text
        raise ValueError(f'Client source changed near {old[:60]}')
    return text.replace(old, new)


def main():
    manifest_path = ROOT / 'docs/integration/source-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for client in ('heartbreaker', 'striker'):
        prefix = f'packages/{client}/src/renderer/src/'
        types_path = ROOT / prefix / 'lib/types.ts'
        types = types_path.read_text(encoding='utf-8')
        types = replace(types, "export type ThinkingLevel = 'none' | 'low' | 'medium' | 'high'", 'export type ThinkingLevel = string')
        types = replace(types, '  thinking_supported?: boolean', '  thinking_supported?: boolean\n  thinking_options?: ThinkingLevel[]')
        slider_path = ROOT / prefix / 'components/ThinkingLevelSlider.tsx'
        slider = slider_path.read_text(encoding='utf-8')
        slider = replace(slider, 'supported = true, onChange, compact = false,', 'supported = true, onChange, compact = false, options,')
        slider = replace(slider, '  value: ThinkingLevel', '  value: ThinkingLevel\n  options?: ThinkingLevel[]')
        slider = replace(slider, '  const idx = Math.max(0, THINKING_LEVELS.indexOf(value))',
            '  const levels = options?.length ? options : THINKING_LEVELS\n  const idx = Math.max(0, levels.indexOf(value))\n  const label = (t.thinkingLevel.names as Record<string, string>)[value] ?? value')
        slider = slider.replace('t.thinkingLevel.names[value]', 'label')
        slider = slider.replace('max={THINKING_LEVELS.length - 1}', 'max={Math.max(1, levels.length - 1)}')
        slider = slider.replace('disabled={!supported}', 'disabled={!supported || levels.length < 2}')
        slider = slider.replace('onChange(THINKING_LEVELS[Number(event.target.value)])', 'onChange(levels[Number(event.target.value)])')
        slider = slider.replace('(THINKING_LEVELS.length - 1)', 'Math.max(1, levels.length - 1)')
        slider = slider.replace('THINKING_LEVELS.map(level', 'levels.map(level')
        slider = replace(slider, 'supported={supported} onChange={onChange}', 'supported={supported} options={options} onChange={onChange}')
        input_path = ROOT / prefix / 'components/InputBar.tsx'
        text = input_path.read_text(encoding='utf-8')
        text = replace(text, 'thinkingPinned, thinkingSupported, onThinking,', 'thinkingPinned, thinkingSupported, thinkingOptions, onThinking,')
        text = replace(text, 'thinkingPinned?: boolean; thinkingSupported: boolean', 'thinkingPinned?: boolean; thinkingSupported: boolean; thinkingOptions?: ThinkingLevel[]')
        text = text.replace('t.thinkingLevel.names[thinking]', '((t.thinkingLevel.names as Record<string, string>)[thinking] ?? thinking)')
        text = replace(text, 'pinned={thinkingPinned} supported onChange={onThinking}', 'pinned={thinkingPinned} supported options={thinkingOptions} onChange={onThinking}')
        text = replace(text, "activeModel?.thinking ?? 'medium'", "activeModel?.thinking ?? 'default'")
        text = replace(text, 'thinkingSupported={activeModel?.thinking_supported !== false}', 'thinkingSupported={activeModel?.thinking_supported !== false}\n                  thinkingOptions={activeModel?.thinking_options}')
        text = replace(text, 'supported={activeModel?.thinking_supported !== false}', 'supported={activeModel?.thinking_supported !== false} options={activeModel?.thinking_options}')
        # A failed save must not claim that a new setting is in force.
        text = replace(text, 'setModelThinking(config, modelId, level).catch(() => {})', '''setModelThinking(config, modelId, level).then(ack => {
      if (ack) {
        setThinkingSet(prev => ({ ...prev, [modelId]: ack.thinking }))
        return
      }
      setThinkingSet(prev => {
        const next = { ...prev }
        if (next[modelId] === level) delete next[modelId]
        return next
      })
      fetchModels(config).then(setModels).catch(() => {})
    })''')
        for path, content in ((types_path, types), (slider_path, slider), (input_path, text)):
            path.write_text(content, encoding='utf-8', newline='\n')
            record = manifest['files'][path.relative_to(ROOT).as_posix()]
            record.update(mode='adapted', reason='Native thinking choices and acknowledged policy saves; original layout retained',
                target_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

import { useId, useState } from 'react'
import { Bookmark, Save } from 'lucide-react'
import { useLocale } from '@/contexts/LocaleContext'
import Button from '@/components/ui/Button'
import ConfirmDialog from '@/components/ui/ConfirmDialog'
import { FORM_INPUT_CLASS } from '@/components/ui/formStyles'

interface PresetToolbarProps {
  label: string
  /** Stored presets; `needsCompletion` marks one saved before it was runnable. */
  entries: { id: string; name: string; needsCompletion?: boolean }[]
  selectedId: string
  dirty: boolean
  /** Locks load/save/delete while a submission is in flight. */
  disabled?: boolean
  onLoad: (id: string) => void
  onSave: (name: string, asNew: boolean) => boolean
  onDelete: () => void
  onReset: () => void
}

/** Common preset actions; values and persistence stay with the workspace owner. */
export default function PresetToolbar({
  label,
  entries,
  selectedId,
  dirty,
  disabled = false,
  onLoad,
  onSave,
  onDelete,
  onReset,
}: PresetToolbarProps) {
  const { t } = useLocale()
  const id = useId()
  const selected = entries.find((entry) => entry.id === selectedId)
  const [editedName, setEditedName] = useState<string | null>(null)
  const [pending, setPending] = useState<{ action: () => void; danger: boolean } | null>(null)
  const [nameError, setNameError] = useState(false)
  const name = editedName ?? selected?.name ?? ''

  function save(asNew: boolean) {
    if (!name.trim()) {
      setNameError(true)
      return
    }
    if (onSave(name.trim(), asNew)) {
      setEditedName(null)
      setNameError(false)
    }
  }
  function replace(action: () => void) {
    if (dirty) setPending({ action, danger: false })
    else action()
  }
  function reset() {
    onReset()
    setEditedName(null)
    setNameError(false)
  }

  return (
    <div
      role="group"
      aria-label={label}
      className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(8rem,0.6fr)] rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-deep)] p-3"
    >
      <div className="flex items-center justify-between gap-2 text-xs sm:col-span-2 text-[var(--text-muted)]">
        <label htmlFor={`${id}-select`} className="flex items-center gap-1.5">
          <Bookmark size={13} />
          {label}
        </label>
        <span role="status">
          {selected?.needsCompletion
            ? t('tasks.library.needsCompletion')
            : dirty
              ? t('tasks.library.unsaved')
              : t('tasks.library.ready')}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <select
          id={`${id}-select`}
          value={selectedId}
          disabled={disabled}
          className={`${FORM_INPUT_CLASS} !w-auto min-w-[10rem] flex-1 basis-full sm:basis-auto`}
          onChange={(event) => {
            const next = event.target.value
            replace(() => {
              onLoad(next)
              setEditedName(null)
              setNameError(false)
            })
          }}
        >
          <option value="">{t('tasks.library.draft')}</option>
          {entries.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.needsCompletion ? `${entry.name} · ${t('tasks.library.needsCompletionShort')}` : entry.name}
            </option>
          ))}
        </select>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled || !selected}
          onClick={() =>
            replace(() => {
              onLoad(selectedId)
              setEditedName(null)
              setNameError(false)
            })
          }
        >
          {t('tasks.library.load')}
        </Button>
        <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => save(false)}>
          <Save size={13} />
          {t('tasks.library.save')}
        </Button>
        <details
          className="relative"
          onKeyDown={(event) => {
            if (event.key === 'Escape') event.currentTarget.open = false
          }}
        >
          <summary className="coarse-target cursor-pointer rounded-[var(--radius-sm)] px-2 py-2 text-xs text-[var(--text-muted)]">
            {t('tasks.library.actions')}
          </summary>
          <div className="absolute right-0 top-full z-30 flex w-44 flex-col gap-1 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-card)] p-2 shadow-[var(--shadow)]">
            <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={() => save(true)}>
              {t('tasks.library.saveAs')}
            </Button>
            <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={() => replace(reset)}>
              {t('tasks.library.reset')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled || !selected}
              onClick={() => setPending({ action: reset, danger: false })}
            >
              {t('tasks.library.new')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled || !selected}
              onClick={() => setPending({ action: onDelete, danger: true })}
            >
              {t('tasks.library.delete')}
            </Button>
          </div>
        </details>
      </div>
      <div>
        <label htmlFor={`${id}-name`} className="sr-only">
          {t('tasks.library.name', { kind: label })}
        </label>
        <input
          id={`${id}-name`}
          value={name}
          maxLength={120}
          placeholder={t('tasks.library.name', { kind: label })}
          disabled={disabled}
          className={FORM_INPUT_CLASS}
          aria-invalid={nameError}
          aria-describedby={nameError ? `${id}-error` : undefined}
          onChange={(event) => {
            setEditedName(event.target.value)
            setNameError(false)
          }}
        />
        {nameError && (
          <p id={`${id}-error`} role="alert" className="mt-1 text-xs text-[var(--danger)]">
            {t('form.validation.required')}
          </p>
        )}
      </div>
      <ConfirmDialog
        open={Boolean(pending)}
        title={t(pending?.danger ? 'tasks.library.delete' : 'tasks.library.replace')}
        message={t(pending?.danger ? 'tasks.library.deleteHint' : 'tasks.library.replaceHint')}
        danger={pending?.danger}
        confirmLabel={t('tasks.library.confirm')}
        cancelLabel={t('tasks.library.cancel')}
        onCancel={() => setPending(null)}
        onConfirm={() => {
          pending?.action()
          setPending(null)
        }}
      />
    </div>
  )
}

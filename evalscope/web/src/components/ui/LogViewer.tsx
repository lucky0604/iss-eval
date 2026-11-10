import { useEffect, useRef, useState } from 'react'
import { useLocale } from '@/contexts/LocaleContext'
import Button from '@/components/ui/Button'

interface Props {
  content: string
  maxHeight?: string
  /** Scroll to the newest line when content grows. Off lets the reader stay put. */
  follow?: boolean
}

/**
 * A bounded log reader.
 *
 * Follows new lines by default. Scrolling away from the bottom pauses the
 * scroll (the content still arrives) and reports how many lines were added,
 * with an explicit way back to the latest line. Scrolling never fights the
 * reader: while paused, growing content does not move the viewport.
 */
export default function LogViewer({ content, maxHeight = '500px', follow = true }: Props) {
  const { t } = useLocale()
  const ref = useRef<HTMLPreElement>(null)
  const [paused, setPaused] = useState(false)
  // The content the reader had seen when they scrolled away. Growth after that
  // point is what the "new lines" counter reports.
  const [seen, setSeen] = useState('')

  const autoFollow = follow && !paused
  const appended = paused && content.startsWith(seen) ? content.slice(seen.length) : ''
  const newLines = appended ? appended.split('\n').filter((line) => line.length > 0).length : 0

  useEffect(() => {
    if (autoFollow && ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight
    }
  }, [content, autoFollow])

  const handleScroll = () => {
    const element = ref.current
    if (!element) return
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight <= 24
    if (atBottom && paused) {
      setPaused(false)
    } else if (!atBottom && !paused) {
      // Capture the current content once, on the transition, so later arrivals
      // can be counted without resetting the baseline on every scroll event.
      setSeen(content)
      setPaused(true)
    }
  }

  const jumpToLatest = () => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
    setPaused(false)
  }

  return (
    <div className="relative">
      <pre
        ref={ref}
        onScroll={handleScroll}
        tabIndex={0}
        aria-label={t('eval.status')}
        className="text-xs p-4 rounded-[var(--radius-sm)] overflow-auto border border-[var(--border)]"
        style={{
          maxHeight,
          background: 'var(--bg-deep)',
          color: 'var(--text-muted)',
          fontFamily: 'var(--font-mono)',
        }}
      >
        {content || t('common.loading')}
      </pre>
      {paused && (
        <div className="absolute bottom-3 right-3">
          <Button variant="outline" size="sm" onClick={jumpToLatest}>
            {newLines > 0 ? t('tasks.detail.newLogLines', { count: newLines }) : t('tasks.detail.jumpToLatest')}
          </Button>
        </div>
      )}
    </div>
  )
}

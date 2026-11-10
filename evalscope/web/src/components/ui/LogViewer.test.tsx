import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { LocaleProvider } from '@/contexts/LocaleContext'
import LogViewer from './LogViewer'

afterEach(() => { cleanup() })

function layout(element: HTMLElement, scrollHeight = 100, clientHeight = 40) {
  Object.defineProperty(element, 'scrollHeight', { configurable: true, value: scrollHeight })
  Object.defineProperty(element, 'clientHeight', { configurable: true, value: clientHeight })
}

function viewer(content: string, follow = true) {
  return (
    <LocaleProvider>
      <LogViewer content={content} follow={follow} />
    </LocaleProvider>
  )
}

describe('LogViewer', () => {
  it('pauses on scroll-away, counts appended lines, and returns to the latest', () => {
    const { container, rerender } = render(viewer('a\nb\n'))
    const pre = container.querySelector('pre')!
    layout(pre)

    // Following the tail: growing content moves the viewport to the bottom.
    rerender(viewer('a\nb\nc\n'))
    expect(pre.scrollTop).toBe(100)

    pre.scrollTop = 0
    fireEvent.scroll(pre)
    expect(screen.getByRole('button', { name: 'Jump to latest' })).toBeInTheDocument()

    // New content must not steal the reader's position.
    rerender(viewer('a\nb\nc\nd\ne\n'))
    expect(pre.scrollTop).toBe(0)
    expect(screen.getByRole('button', { name: 'New lines: 2' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'New lines: 2' }))
    expect(pre.scrollTop).toBe(100)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('resumes following when the reader scrolls back to the bottom', () => {
    const { container } = render(viewer('a\n'))
    const pre = container.querySelector('pre')!
    layout(pre)

    pre.scrollTop = 0
    fireEvent.scroll(pre)
    expect(screen.getByRole('button', { name: 'Jump to latest' })).toBeInTheDocument()

    // 100 - 60 - 40 = 0: the reader is at the bottom again.
    pre.scrollTop = 60
    fireEvent.scroll(pre)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('never auto-scrolls when following is switched off', () => {
    const { container, rerender } = render(viewer('a\n', false))
    const pre = container.querySelector('pre')!
    layout(pre)
    expect(pre.scrollTop).toBe(0)

    rerender(viewer('a\nb\nc\n', false))
    expect(pre.scrollTop).toBe(0)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})

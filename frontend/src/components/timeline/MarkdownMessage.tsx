import type { ReactNode } from 'react'
import { isValidElement } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { CopyButton } from './CopyButton'

interface MarkdownMessageProps {
  text: string
}

function textFromNode(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node)
  }
  if (Array.isArray(node)) {
    return node.map(textFromNode).join('')
  }
  if (isValidElement(node)) {
    const props = node.props as { children?: ReactNode }
    return textFromNode(props.children)
  }
  return ''
}

const markdownComponents: Components = {
  a({ node: _node, ...props }) {
    void _node
    return <a {...props} rel="noopener noreferrer" target="_blank" />
  },
  blockquote({ node: _node, ...props }) {
    void _node
    return <blockquote {...props} className="my-3 border-l-2 border-border pl-4 text-muted-foreground" />
  },
  code({ node: _node, className, ...props }) {
    void _node
    return <code {...props} className={`${className ?? ''} rounded bg-muted px-1 py-0.5 font-mono text-[0.9em]`} />
  },
  h1({ node: _node, ...props }) {
    void _node
    return <h1 {...props} className="mb-3 mt-5 text-xl font-semibold first:mt-0" />
  },
  h2({ node: _node, ...props }) {
    void _node
    return <h2 {...props} className="mb-2 mt-5 text-lg font-semibold first:mt-0" />
  },
  h3({ node: _node, ...props }) {
    void _node
    return <h3 {...props} className="mb-2 mt-4 text-base font-semibold first:mt-0" />
  },
  li({ node: _node, ...props }) {
    void _node
    return <li {...props} className="my-1" />
  },
  ol({ node: _node, ...props }) {
    void _node
    return <ol {...props} className="my-3 list-decimal space-y-1 pl-5" />
  },
  p({ node: _node, ...props }) {
    void _node
    return <p {...props} className="my-3 first:mt-0 last:mb-0" />
  },
  pre({ node: _node, children, ...props }) {
    void _node
    const value = textFromNode(children).replace(/\n$/, '')
    return (
      <div className="my-3 overflow-hidden rounded-lg border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border px-3 py-1.5 text-[11px] text-muted-foreground">
          <span>Code</span>
          <CopyButton value={value} />
        </div>
        <pre {...props} className="max-w-full overflow-x-auto p-3 text-xs leading-5">
          {children}
        </pre>
      </div>
    )
  },
  table({ node: _node, ...props }) {
    void _node
    return <table {...props} className="my-3 block max-w-full overflow-x-auto text-left text-sm" />
  },
  td({ node: _node, ...props }) {
    void _node
    return <td {...props} className="border border-border px-2 py-1" />
  },
  th({ node: _node, ...props }) {
    void _node
    return <th {...props} className="border border-border bg-muted px-2 py-1 font-medium" />
  },
  ul({ node: _node, ...props }) {
    void _node
    return <ul {...props} className="my-3 list-disc space-y-1 pl-5" />
  },
}

export function MarkdownMessage({ text }: MarkdownMessageProps) {
  return (
    <div className="min-w-0 text-sm leading-6">
      <Markdown components={markdownComponents} remarkPlugins={[remarkGfm]} skipHtml>
        {text}
      </Markdown>
    </div>
  )
}

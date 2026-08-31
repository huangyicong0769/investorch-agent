import { useEffect, useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'

import { resolveApproval } from '../../api/client'
import type { ApprovalRequest, ResolveApprovalResponse } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { formatJsonValue } from '../../lib/timeline/project'
import { Button } from '@/components/ui/button'

interface ApprovalCardProps {
  approvals: ApprovalRequest[]
  sessionId: string
}

interface ResolveVariables {
  approvalId: string
  approved: boolean
  sessionId: string
}

interface ResolutionState {
  error: unknown | null
  resolving: boolean
  sessionId: string
}

export function ApprovalCard({ approvals, sessionId }: ApprovalCardProps) {
  const selectedApprovals = useMemo(
    () => approvals.filter((approval) => approval.session_id === sessionId),
    [approvals, sessionId],
  )
  const approvalIds = useMemo(
    () => selectedApprovals.map((approval) => approval.approval_id),
    [selectedApprovals],
  )
  const [activeApprovalId, setActiveApprovalId] = useState<string | null>(null)
  const [resolutionById, setResolutionById] = useState<Map<string, ResolutionState>>(
    () => new Map(),
  )

  const resolveMutation = useMutation<ResolveApprovalResponse, Error, ResolveVariables>({
    mutationFn: ({ approvalId, approved }) => resolveApproval(approvalId, { approved }),
    onError: (error, variables) => {
      setResolutionById((current) => {
        const existing = current.get(variables.approvalId)
        if (existing?.sessionId !== variables.sessionId) {
          return current
        }
        const next = new Map(current)
        next.set(variables.approvalId, {
          error,
          resolving: false,
          sessionId: variables.sessionId,
        })
        return next
      })
    },
  })

  useEffect(() => {
    const pendingIds = new Set(approvalIds)
    setActiveApprovalId((current) =>
      current !== null && pendingIds.has(current) ? current : (approvalIds[0] ?? null),
    )
    setResolutionById((current) => {
      let next: Map<string, ResolutionState> | null = null
      for (const [approvalId, resolution] of current) {
        if (resolution.sessionId === sessionId && !pendingIds.has(approvalId)) {
          next ??= new Map(current)
          next.delete(approvalId)
        }
      }
      return next ?? current
    })
  }, [approvalIds, sessionId])

  if (selectedApprovals.length === 0) {
    return null
  }

  const activeIndex = Math.max(
    0,
    selectedApprovals.findIndex((approval) => approval.approval_id === activeApprovalId),
  )
  const approval = selectedApprovals[activeIndex]
  const resolution = resolutionById.get(approval.approval_id)
  const resolving = resolution?.resolving === true

  const resolve = (approved: boolean) => {
    const variables = {
      approvalId: approval.approval_id,
      approved,
      sessionId: approval.session_id,
    }
    setResolutionById((current) =>
      new Map(current).set(approval.approval_id, {
        error: null,
        resolving: true,
        sessionId: approval.session_id,
      }),
    )
    resolveMutation.mutate(variables)
  }

  return (
    <section
      aria-labelledby={`approval-title-${approval.approval_id}`}
      className="rounded-xl border border-border bg-card px-4 py-3 shadow-sm"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold" id={`approval-title-${approval.approval_id}`}>
          Approval required · {activeIndex + 1} of {selectedApprovals.length}
        </h2>
        {selectedApprovals.length > 1 ? (
          <div aria-label="Pending approvals" className="flex shrink-0 items-center gap-1">
            <Button
              size={null}
              variant={null}
              aria-label="Previous approval"
              className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
              disabled={activeIndex === 0}
              onClick={() => setActiveApprovalId(selectedApprovals[activeIndex - 1].approval_id)}
              type="button"
            >
              Previous
            </Button>
            <Button
              size={null}
              variant={null}
              aria-label="Next approval"
              className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
              disabled={activeIndex === selectedApprovals.length - 1}
              onClick={() => setActiveApprovalId(selectedApprovals[activeIndex + 1].approval_id)}
              type="button"
            >
              Next
            </Button>
          </div>
        ) : null}
      </div>

      <p className="mt-2 text-sm">
        QMT Agent wants to run <span className="font-medium">{approval.tool_name}</span>
      </p>
      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted px-3 py-2 font-mono text-xs leading-5">
        {approval.arguments === null ? 'No arguments provided.' : formatJsonValue(approval.arguments)}
      </pre>

      {approval.review_reason !== null ? (
        <div className="mt-3 border-l-2 border-border pl-3 text-xs">
          <p className="font-medium">AutoReview</p>
          <p className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
            {approval.review_reason}
          </p>
        </div>
      ) : null}

      <div className="mt-3 flex items-center justify-end gap-2">
        {resolving ? (
          <span className="mr-auto text-xs text-muted-foreground" role="status">
            Resolving…
          </span>
        ) : null}
        <Button
          size={null}
          variant={null}
          className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
          disabled={resolving}
          onClick={() => resolve(false)}
          type="button"
        >
          Reject
        </Button>
        <Button
          size={null}
          variant={null}
          className="rounded-lg bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:cursor-not-allowed disabled:opacity-60"
          disabled={resolving}
          onClick={() => resolve(true)}
          type="button"
        >
          Approve
        </Button>
      </div>

      {resolution?.error ? (
        <p className="mt-2 text-xs text-red-700" role="alert">
          {errorMessage(resolution.error, 'The approval could not be resolved. Try again.')}
        </p>
      ) : null}
    </section>
  )
}

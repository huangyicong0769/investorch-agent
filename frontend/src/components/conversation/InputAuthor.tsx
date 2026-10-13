import { Link } from 'react-router-dom'

import type { InputOrigin } from '../../api/types'
import { sessionPath, shortSessionId } from '../../lib/session'
import { CopyButton } from '../ui/CopyButton'

export function InputAuthor({ origin }: { origin?: InputOrigin }) {
  if (!origin) return <>You</>

  return (
    <span className="inline-flex items-center gap-1">
      <Link className="hover:underline" title={origin.sender_session_id} to={sessionPath(origin.sender_session_id)}>
        Agent · {shortSessionId(origin.sender_session_id)}
      </Link>
      <CopyButton value={origin.sender_session_id} />
    </span>
  )
}

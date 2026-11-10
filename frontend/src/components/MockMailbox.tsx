import { useMemo } from 'react'
import { USE_MOCK } from '../api'
import { readMockMailbox } from '../api/mock-account'

// 샘플 데이터(mock) 모드 전용. 실제 서버는 인증·재설정 링크를 메일로 보내지만 mock에는 메일이 없어서, 보낸 것으로 친 링크를
// 여기서 눌러 볼 수 있게 한다(개발 서버의 backend/backups/dev_mail.log와 같은 역할). 실제 모드에서는 아무것도 그리지 않는다.
// refreshKey가 바뀔 때(메일을 보낸 직후) 다시 읽는다.
export default function MockMailbox({ refreshKey }: { refreshKey?: unknown }) {
  // refreshKey는 읽는 시점만 바꾸는 값이라 deps에만 둔다
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const mails = useMemo(() => (USE_MOCK ? readMockMailbox().slice(0, 3) : []), [refreshKey])
  if (!USE_MOCK) return null
  return (
    <div className="mock-mailbox" aria-label="샘플 메일함">
      <p className="muted small">샘플 데이터 모드라 메일이 실제로 가지 않아요. 보낸 것으로 친 메일의 링크예요.</p>
      {mails.length === 0 ? (
        <p className="muted small">아직 보낸 메일이 없어요.</p>
      ) : (
        <ul>
          {mails.map((mail) => (
            <li key={mail.id}>
              <span className="muted small">{mail.subject}</span>{' '}
              <a href={mail.link} className="small">
                링크 열기
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

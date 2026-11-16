// 화면 읽기 프로그램용 알림 영역. 화면에는 보이지 않는다.
// 알림 영역은 처음부터 화면에 있어야 안에 들어온 글이 읽히므로, 결과가 나올 때 새로 그리지 않고 항상 그려 두고 message만 바꾼다.
// (진행 문구가 결과로 바뀌는 것, 폼 제출 결과처럼 눈에는 보이지만 소리로는 알 수 없는 변화를 알리는 데 쓴다.)
export default function LiveStatus({ message }: { message: string }) {
  return (
    <p className="sr-only" role="status">
      {message}
    </p>
  )
}

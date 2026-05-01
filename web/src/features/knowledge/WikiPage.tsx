import { useSearchParams } from 'react-router-dom';
import WikiBrowser from './WikiBrowser';

export default function WikiPage() {
  const [searchParams] = useSearchParams();
  const page = searchParams.get('page') || undefined;

  return (
    <div style={{ padding: 0 }}>
      <WikiBrowser initialPage={page} />
    </div>
  );
}

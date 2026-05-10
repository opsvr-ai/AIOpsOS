import { Image, theme } from 'antd';

export interface FeedbackImageGalleryProps {
  /** Array of image URLs to display */
  images: string[];
}

/**
 * FeedbackImageGallery component displays a read-only gallery of feedback images.
 * Used in the feedback detail view to show images attached to a feedback submission.
 *
 * Features:
 * - Responsive thumbnail grid layout
 * - Click-to-preview with Ant Design Image.PreviewGroup
 * - Zoom, rotate controls in preview modal
 * - Conditionally renders nothing when images array is empty
 *
 * @example
 * ```tsx
 * <FeedbackImageGallery images={['/uploads/feedbacks/abc123.png', '/uploads/feedbacks/def456.jpg']} />
 * ```
 *
 * @validates Requirements 6.1, 6.2, 6.3
 */
export default function FeedbackImageGallery({ images }: FeedbackImageGalleryProps) {
  const { token } = theme.useToken();

  // Requirement 6.4: Don't display image area if no images
  if (!images || images.length === 0) {
    return null;
  }

  // Prepare preview items for the PreviewGroup
  const previewItems = images.map((url) => ({ src: url }));

  return (
    <Image.PreviewGroup
      items={previewItems}
      preview={{
        toolbarRender: (
          _,
          { transform: { scale }, actions: { onZoomOut, onZoomIn, onRotateLeft, onRotateRight } }
        ) => (
          <div style={{ display: 'flex', gap: 12 }}>
            <span onClick={onZoomOut} style={{ cursor: 'pointer', color: '#fff' }}>
              缩小
            </span>
            <span onClick={onZoomIn} style={{ cursor: 'pointer', color: '#fff' }}>
              放大
            </span>
            <span onClick={onRotateLeft} style={{ cursor: 'pointer', color: '#fff' }}>
              左旋
            </span>
            <span onClick={onRotateRight} style={{ cursor: 'pointer', color: '#fff' }}>
              右旋
            </span>
            <span style={{ color: '#fff' }}>{Math.round(scale * 100)}%</span>
          </div>
        ),
      }}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(80px, 1fr))',
          gap: 8,
          maxWidth: 440, // Max 5 thumbnails per row at 80px each + gaps
        }}
      >
        {images.map((url, index) => (
          <div
            key={`${url}-${index}`}
            style={{
              position: 'relative',
              aspectRatio: '1',
              borderRadius: token.borderRadius,
              overflow: 'hidden',
              border: `1px solid ${token.colorBorder}`,
              background: token.colorBgContainer,
            }}
          >
            <Image
              src={url}
              alt={`反馈图片 ${index + 1}`}
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'cover',
                cursor: 'pointer',
              }}
              preview={{
                mask: (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: '100%',
                      height: '100%',
                    }}
                  >
                    预览
                  </div>
                ),
              }}
            />
          </div>
        ))}
      </div>
    </Image.PreviewGroup>
  );
}

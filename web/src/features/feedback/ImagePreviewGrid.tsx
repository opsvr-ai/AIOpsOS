import { useState } from 'react';
import { Image, Spin, theme } from 'antd';
import {
  LoadingOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  DeleteOutlined,
} from '@ant-design/icons';
import type { PendingImage } from './ImageUploader';

export interface ImagePreviewGridProps {
  /** Array of pending images to display */
  images: PendingImage[];
  /** Callback when delete button is clicked */
  onDelete: (id: string) => void;
  /** Callback when image is clicked for preview */
  onPreview?: (image: PendingImage) => void;
}

/**
 * Renders a status indicator overlay based on the image upload status.
 */
function StatusIndicator({ status, error }: { status: PendingImage['status']; error?: string }) {
  const { token } = theme.useToken();

  if (status === 'uploading') {
    return (
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgba(0, 0, 0, 0.45)',
          borderRadius: token.borderRadius,
        }}
      >
        <Spin indicator={<LoadingOutlined style={{ fontSize: 24, color: '#fff' }} spin />} />
      </div>
    );
  }

  if (status === 'uploaded') {
    return (
      <div
        style={{
          position: 'absolute',
          bottom: 4,
          right: 4,
          width: 20,
          height: 20,
          borderRadius: '50%',
          background: token.colorSuccess,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <CheckCircleFilled style={{ fontSize: 12, color: '#fff' }} />
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgba(255, 77, 79, 0.15)',
          borderRadius: token.borderRadius,
          border: `1px solid ${token.colorError}`,
        }}
      >
        <CloseCircleFilled style={{ fontSize: 24, color: token.colorError }} />
        {error && (
          <div
            style={{
              marginTop: 4,
              fontSize: 10,
              color: token.colorError,
              textAlign: 'center',
              padding: '0 4px',
              maxWidth: '100%',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {error}
          </div>
        )}
      </div>
    );
  }

  // status === 'pending' - no indicator needed
  return null;
}

interface ThumbnailItemProps {
  image: PendingImage;
  index: number;
  onDelete: (id: string) => void;
  onPreview?: (image: PendingImage) => void;
}

/**
 * Individual thumbnail item with hover-to-show delete button.
 * Uses React state to manage hover visibility for the delete button.
 */
function ThumbnailItem({ image, index, onDelete, onPreview }: ThumbnailItemProps) {
  const { token } = theme.useToken();
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      style={{
        position: 'relative',
        aspectRatio: '1',
        borderRadius: token.borderRadius,
        overflow: 'hidden',
        border: `1px solid ${image.status === 'error' ? token.colorError : token.colorBorder}`,
        background: token.colorBgContainer,
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Thumbnail image - clicking opens full-size preview modal */}
      <Image
        src={image.previewUrl}
        alt={`Preview ${index + 1}`}
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
        onClick={() => onPreview?.(image)}
      />

      {/* Status indicator overlay */}
      <StatusIndicator status={image.status} error={image.error} />

      {/* Delete button - shown on thumbnail hover, positioned at top-right */}
      <div
        className="delete-button"
        style={{
          position: 'absolute',
          top: 4,
          right: 4,
          width: 24,
          height: 24,
          borderRadius: '50%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgba(0, 0, 0, 0.65)',
          opacity: isHovered ? 1 : 0,
          transition: 'opacity 0.2s ease',
          cursor: 'pointer',
          zIndex: 10,
        }}
        onClick={(e) => {
          e.stopPropagation();
          onDelete(image.id);
        }}
      >
        <DeleteOutlined style={{ fontSize: 14, color: '#fff' }} />
      </div>
    </div>
  );
}

/**
 * ImagePreviewGrid component displays a responsive grid of image thumbnails.
 * Each thumbnail shows the upload status and provides delete functionality.
 *
 * Features:
 * - Responsive grid layout (adapts to container width)
 * - Upload status indicators (pending, uploading, uploaded, error)
 * - Delete button overlay on hover
 * - Click to preview full-size image using Ant Design Image.PreviewGroup modal
 *
 * @example
 * ```tsx
 * <ImagePreviewGrid
 *   images={pendingImages}
 *   onDelete={(id) => handleDelete(id)}
 *   onPreview={(image) => handlePreview(image)}
 * />
 * ```
 */
export default function ImagePreviewGrid({ images, onDelete, onPreview }: ImagePreviewGridProps) {
  if (images.length === 0) {
    return null;
  }

  // Collect all preview URLs for the PreviewGroup
  const previewItems = images.map((image) => ({
    src: image.previewUrl,
  }));

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
        {images.map((image, index) => (
          <ThumbnailItem
            key={image.id}
            image={image}
            index={index}
            onDelete={onDelete}
            onPreview={onPreview}
          />
        ))}
      </div>
    </Image.PreviewGroup>
  );
}

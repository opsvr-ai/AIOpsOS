import { useCallback, useRef, useEffect } from 'react';
import { Upload, Button, App, theme } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { UploadProps } from 'antd';
import ImagePreviewGrid from './ImagePreviewGrid';

/**
 * Represents an image pending upload in the feedback form.
 * Tracks the image through its lifecycle from selection to upload completion.
 */
export interface PendingImage {
  /** Client-generated UUID for tracking */
  id: string;
  /** The actual file object */
  file: File;
  /** Object URL for preview (created via URL.createObjectURL) */
  previewUrl: string;
  /** Current status of the image */
  status: 'pending' | 'uploading' | 'uploaded' | 'error';
  /** Server URL after successful upload */
  uploadedUrl?: string;
  /** Error message if upload failed */
  error?: string;
}

export interface ImageUploaderProps {
  /** Array of pending images */
  images: PendingImage[];
  /** Callback when images change (add/remove) */
  onChange: (images: PendingImage[]) => void;
  /** Maximum number of images allowed (default: 5) */
  maxCount?: number;
  /** Maximum file size in MB (default: 5) */
  maxSizeMB?: number;
  /** Whether the uploader is disabled */
  disabled?: boolean;
}

/** Allowed image MIME types */
export const ALLOWED_TYPES = ['image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/webp'];

/** Allowed file extensions for the file picker */
export const ACCEPT_EXTENSIONS = '.png,.jpg,.jpeg,.gif,.webp';

/**
 * Generates a unique ID for tracking pending images.
 */
export function generateId(): string {
  return `img-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

/**
 * Validates an image file for type and size constraints.
 * @param file - The file to validate
 * @param maxSizeMB - Maximum file size in MB
 * @returns Error message if invalid, null if valid
 */
export function validateImageFile(file: File, maxSizeMB: number): string | null {
  // Check file type
  if (!ALLOWED_TYPES.includes(file.type)) {
    return '仅支持 PNG、JPG、JPEG、GIF、WebP 格式';
  }

  // Check file size (convert MB to bytes)
  const maxSizeBytes = maxSizeMB * 1024 * 1024;
  if (file.size > maxSizeBytes) {
    return `图片大小不能超过 ${maxSizeMB}MB`;
  }

  return null;
}

/**
 * Creates a PendingImage object from a File.
 * @param file - The file to create a pending image from
 * @returns A new PendingImage object
 */
export function createPendingImage(file: File): PendingImage {
  return {
    id: generateId(),
    file,
    previewUrl: URL.createObjectURL(file),
    status: 'pending',
  };
}

/**
 * Removes an image from the pending list and revokes its object URL to prevent memory leaks.
 * This function should be used as the delete handler for ImagePreviewGrid.
 * 
 * @param images - Current array of pending images
 * @param idToDelete - ID of the image to delete
 * @returns New array with the image removed (original array is not modified)
 * 
 * @example
 * ```tsx
 * const handleDelete = (id: string) => {
 *   setImages(deletePendingImage(images, id));
 * };
 * ```
 */
export function deletePendingImage(images: PendingImage[], idToDelete: string): PendingImage[] {
  // Find the image to delete
  const imageToDelete = images.find((img) => img.id === idToDelete);
  
  // Revoke the object URL to prevent memory leaks
  if (imageToDelete) {
    URL.revokeObjectURL(imageToDelete.previewUrl);
  }
  
  // Return new array without the deleted image
  return images.filter((img) => img.id !== idToDelete);
}

/**
 * ImageUploader component for handling image uploads in the feedback form.
 * Supports both file picker selection and clipboard paste (Ctrl+V).
 * 
 * This is a controlled component - the parent manages the images state.
 * 
 * @example
 * ```tsx
 * const [images, setImages] = useState<PendingImage[]>([]);
 * 
 * <ImageUploader
 *   images={images}
 *   onChange={setImages}
 *   maxCount={5}
 *   maxSizeMB={5}
 * />
 * ```
 */
export default function ImageUploader({
  images,
  onChange,
  maxCount = 5,
  maxSizeMB = 5,
  disabled = false,
}: ImageUploaderProps) {
  const { message } = App.useApp();
  const { token } = theme.useToken();
  const containerRef = useRef<HTMLDivElement>(null);

  /**
   * Adds a validated image to the pending list.
   * Creates a preview URL and initializes the image state.
   */
  const addImage = useCallback(
    (file: File) => {
      // Check max count
      if (images.length >= maxCount) {
        message.error(`最多只能上传 ${maxCount} 张图片`);
        return;
      }

      // Validate the image
      const error = validateImageFile(file, maxSizeMB);
      if (error) {
        message.error(error);
        return;
      }

      // Create pending image and add to list
      const newImage = createPendingImage(file);
      onChange([...images, newImage]);
    },
    [images, maxCount, maxSizeMB, onChange, message]
  );

  /**
   * Handles paste events to capture images from clipboard.
   */
  const handlePaste = useCallback(
    (event: ClipboardEvent) => {
      if (disabled) return;

      const clipboardData = event.clipboardData;
      if (!clipboardData) return;

      // Look for image data in clipboard items
      const items = Array.from(clipboardData.items);
      const imageItem = items.find((item) => item.type.startsWith('image/'));

      if (!imageItem) {
        // No image in clipboard - ignore silently (per requirement 1.3)
        return;
      }

      const file = imageItem.getAsFile();
      if (file) {
        // Prevent default paste behavior when we handle an image
        event.preventDefault();
        addImage(file);
      }
    },
    [disabled, addImage]
  );

  /**
   * Handles file selection from the file picker.
   */
  const handleBeforeUpload: UploadProps['beforeUpload'] = useCallback(
    (file: File) => {
      addImage(file);
      // Return false to prevent automatic upload - we handle it manually
      return false;
    },
    [addImage]
  );

  // Set up paste event listener
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Listen for paste events on the container
    container.addEventListener('paste', handlePaste);

    return () => {
      container.removeEventListener('paste', handlePaste);
    };
  }, [handlePaste]);

  // Clean up preview URLs when component unmounts
  useEffect(() => {
    return () => {
      images.forEach((img) => {
        URL.revokeObjectURL(img.previewUrl);
      });
    };
    // Only run cleanup on unmount, not on every images change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * Handles deletion of a pending image.
   * Removes the image from the list and revokes its object URL to prevent memory leaks.
   */
  const handleDelete = useCallback(
    (id: string) => {
      onChange(deletePendingImage(images, id));
    },
    [images, onChange]
  );

  const canAddMore = images.length < maxCount;

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      style={{
        outline: 'none',
        padding: '8px 0',
      }}
    >
      <Upload
        accept={ACCEPT_EXTENSIONS}
        beforeUpload={handleBeforeUpload}
        showUploadList={false}
        disabled={disabled || !canAddMore}
        multiple={false}
      >
        <Button
          icon={<PlusOutlined />}
          disabled={disabled || !canAddMore}
          style={{
            borderStyle: 'dashed',
            color: token.colorTextSecondary,
          }}
        >
          添加图片
        </Button>
      </Upload>

      {images.length > 0 && (
        <div
          style={{
            marginTop: 8,
            fontSize: 12,
            color: token.colorTextTertiary,
          }}
        >
          已添加 {images.length}/{maxCount} 张图片（支持粘贴截图 Ctrl+V）
        </div>
      )}

      {images.length === 0 && (
        <div
          style={{
            marginTop: 8,
            fontSize: 12,
            color: token.colorTextTertiary,
          }}
        >
          支持粘贴截图（Ctrl+V）或点击上传，最多 {maxCount} 张，单张不超过 {maxSizeMB}MB
        </div>
      )}

      {/* Image preview grid with delete functionality */}
      {images.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <ImagePreviewGrid images={images} onDelete={handleDelete} />
        </div>
      )}
    </div>
  );
}

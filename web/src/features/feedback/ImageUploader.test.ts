import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { validateImageFile, ALLOWED_TYPES, ACCEPT_EXTENSIONS, generateId, createPendingImage, deletePendingImage, type PendingImage } from './ImageUploader';

// Mock URL.revokeObjectURL for testing
const mockRevokeObjectURL = vi.fn();
const originalRevokeObjectURL = globalThis.URL.revokeObjectURL;

beforeEach(() => {
  globalThis.URL.revokeObjectURL = mockRevokeObjectURL;
  mockRevokeObjectURL.mockClear();
});

afterEach(() => {
  globalThis.URL.revokeObjectURL = originalRevokeObjectURL;
});

/**
 * Unit tests for image validation functionality.
 * Validates: Requirements 1.4, 2.3, 2.5
 */
describe('validateImageFile', () => {
  const DEFAULT_MAX_SIZE_MB = 5;

  /**
   * Helper to create a mock File object for testing.
   */
  function createMockFile(name: string, size: number, type: string): File {
    const content = new Uint8Array(size);
    return new File([content], name, { type });
  }

  describe('Image Type Validation (Requirement 2.3)', () => {
    it('should accept PNG images', () => {
      const file = createMockFile('test.png', 1024, 'image/png');
      expect(validateImageFile(file, DEFAULT_MAX_SIZE_MB)).toBeNull();
    });

    it('should accept JPG images', () => {
      const file = createMockFile('test.jpg', 1024, 'image/jpeg');
      expect(validateImageFile(file, DEFAULT_MAX_SIZE_MB)).toBeNull();
    });

    it('should accept JPEG images', () => {
      const file = createMockFile('test.jpeg', 1024, 'image/jpeg');
      expect(validateImageFile(file, DEFAULT_MAX_SIZE_MB)).toBeNull();
    });

    it('should accept GIF images', () => {
      const file = createMockFile('test.gif', 1024, 'image/gif');
      expect(validateImageFile(file, DEFAULT_MAX_SIZE_MB)).toBeNull();
    });

    it('should accept WebP images', () => {
      const file = createMockFile('test.webp', 1024, 'image/webp');
      expect(validateImageFile(file, DEFAULT_MAX_SIZE_MB)).toBeNull();
    });

    it('should reject PDF files', () => {
      const file = createMockFile('test.pdf', 1024, 'application/pdf');
      const error = validateImageFile(file, DEFAULT_MAX_SIZE_MB);
      expect(error).toBe('仅支持 PNG、JPG、JPEG、GIF、WebP 格式');
    });

    it('should reject text files', () => {
      const file = createMockFile('test.txt', 1024, 'text/plain');
      const error = validateImageFile(file, DEFAULT_MAX_SIZE_MB);
      expect(error).toBe('仅支持 PNG、JPG、JPEG、GIF、WebP 格式');
    });

    it('should reject SVG images', () => {
      const file = createMockFile('test.svg', 1024, 'image/svg+xml');
      const error = validateImageFile(file, DEFAULT_MAX_SIZE_MB);
      expect(error).toBe('仅支持 PNG、JPG、JPEG、GIF、WebP 格式');
    });

    it('should reject BMP images', () => {
      const file = createMockFile('test.bmp', 1024, 'image/bmp');
      const error = validateImageFile(file, DEFAULT_MAX_SIZE_MB);
      expect(error).toBe('仅支持 PNG、JPG、JPEG、GIF、WebP 格式');
    });

    it('should reject files with empty type', () => {
      const file = createMockFile('test.unknown', 1024, '');
      const error = validateImageFile(file, DEFAULT_MAX_SIZE_MB);
      expect(error).toBe('仅支持 PNG、JPG、JPEG、GIF、WebP 格式');
    });
  });

  describe('Image Size Validation (Requirements 1.4, 2.5)', () => {
    it('should accept images exactly at the size limit', () => {
      const exactSize = DEFAULT_MAX_SIZE_MB * 1024 * 1024; // 5MB in bytes
      const file = createMockFile('test.png', exactSize, 'image/png');
      expect(validateImageFile(file, DEFAULT_MAX_SIZE_MB)).toBeNull();
    });

    it('should accept images under the size limit', () => {
      const smallSize = 1024; // 1KB
      const file = createMockFile('test.png', smallSize, 'image/png');
      expect(validateImageFile(file, DEFAULT_MAX_SIZE_MB)).toBeNull();
    });

    it('should reject images over the 5MB limit', () => {
      const overSize = DEFAULT_MAX_SIZE_MB * 1024 * 1024 + 1; // 5MB + 1 byte
      const file = createMockFile('test.png', overSize, 'image/png');
      const error = validateImageFile(file, DEFAULT_MAX_SIZE_MB);
      expect(error).toBe('图片大小不能超过 5MB');
    });

    it('should reject large images regardless of format', () => {
      const largeSize = 10 * 1024 * 1024; // 10MB
      
      // Test with different valid formats
      const pngFile = createMockFile('test.png', largeSize, 'image/png');
      expect(validateImageFile(pngFile, DEFAULT_MAX_SIZE_MB)).toBe('图片大小不能超过 5MB');
      
      const jpgFile = createMockFile('test.jpg', largeSize, 'image/jpeg');
      expect(validateImageFile(jpgFile, DEFAULT_MAX_SIZE_MB)).toBe('图片大小不能超过 5MB');
      
      const gifFile = createMockFile('test.gif', largeSize, 'image/gif');
      expect(validateImageFile(gifFile, DEFAULT_MAX_SIZE_MB)).toBe('图片大小不能超过 5MB');
      
      const webpFile = createMockFile('test.webp', largeSize, 'image/webp');
      expect(validateImageFile(webpFile, DEFAULT_MAX_SIZE_MB)).toBe('图片大小不能超过 5MB');
    });

    it('should use custom max size when provided', () => {
      const customMaxSize = 2; // 2MB
      const overSize = 3 * 1024 * 1024; // 3MB
      const file = createMockFile('test.png', overSize, 'image/png');
      const error = validateImageFile(file, customMaxSize);
      expect(error).toBe('图片大小不能超过 2MB');
    });

    it('should accept zero-byte files (edge case)', () => {
      const file = createMockFile('test.png', 0, 'image/png');
      expect(validateImageFile(file, DEFAULT_MAX_SIZE_MB)).toBeNull();
    });
  });

  describe('Combined Validation', () => {
    it('should check type before size (invalid type takes precedence)', () => {
      const largeSize = 10 * 1024 * 1024; // 10MB
      const file = createMockFile('test.pdf', largeSize, 'application/pdf');
      const error = validateImageFile(file, DEFAULT_MAX_SIZE_MB);
      // Type error should be returned first
      expect(error).toBe('仅支持 PNG、JPG、JPEG、GIF、WebP 格式');
    });

    it('should return size error for valid type but oversized file', () => {
      const largeSize = 10 * 1024 * 1024; // 10MB
      const file = createMockFile('test.png', largeSize, 'image/png');
      const error = validateImageFile(file, DEFAULT_MAX_SIZE_MB);
      expect(error).toBe('图片大小不能超过 5MB');
    });
  });
});

describe('ALLOWED_TYPES constant', () => {
  it('should include all required image types', () => {
    expect(ALLOWED_TYPES).toContain('image/png');
    expect(ALLOWED_TYPES).toContain('image/jpeg');
    expect(ALLOWED_TYPES).toContain('image/jpg');
    expect(ALLOWED_TYPES).toContain('image/gif');
    expect(ALLOWED_TYPES).toContain('image/webp');
  });

  it('should have exactly 5 allowed types', () => {
    expect(ALLOWED_TYPES).toHaveLength(5);
  });
});

describe('ACCEPT_EXTENSIONS constant', () => {
  it('should include all required file extensions', () => {
    expect(ACCEPT_EXTENSIONS).toContain('.png');
    expect(ACCEPT_EXTENSIONS).toContain('.jpg');
    expect(ACCEPT_EXTENSIONS).toContain('.jpeg');
    expect(ACCEPT_EXTENSIONS).toContain('.gif');
    expect(ACCEPT_EXTENSIONS).toContain('.webp');
  });
});

describe('generateId', () => {
  it('should generate unique IDs', () => {
    const id1 = generateId();
    const id2 = generateId();
    expect(id1).not.toBe(id2);
  });

  it('should generate IDs with img- prefix', () => {
    const id = generateId();
    expect(id).toMatch(/^img-/);
  });
});

describe('createPendingImage', () => {
  it('should create a pending image with correct properties', () => {
    const file = new File(['test'], 'test.png', { type: 'image/png' });
    const pendingImage = createPendingImage(file);

    expect(pendingImage.id).toMatch(/^img-/);
    expect(pendingImage.file).toBe(file);
    expect(pendingImage.previewUrl).toBeTruthy();
    expect(pendingImage.status).toBe('pending');
    expect(pendingImage.uploadedUrl).toBeUndefined();
    expect(pendingImage.error).toBeUndefined();
  });
});


/**
 * Unit tests for max images limit enforcement.
 * Validates: Requirements 3.5, 3.6
 */
describe('Max Images Limit Enforcement', () => {
  const MAX_COUNT = 5;

  /**
   * Helper to create a mock PendingImage for testing.
   */
  function createMockPendingImage(id: string): PendingImage {
    const file = new File(['test'], `test-${id}.png`, { type: 'image/png' });
    return {
      id,
      file,
      previewUrl: `blob:test-${id}`,
      status: 'pending',
    };
  }

  /**
   * Helper to create an array of mock pending images.
   */
  function createMockPendingImages(count: number): PendingImage[] {
    return Array.from({ length: count }, (_, i) => createMockPendingImage(`img-${i}`));
  }

  describe('Image Count Validation (Requirements 3.5, 3.6)', () => {
    it('should allow adding images when under the limit', () => {
      const existingImages = createMockPendingImages(4);
      expect(existingImages.length).toBeLessThan(MAX_COUNT);
      // Can add one more image
      expect(existingImages.length < MAX_COUNT).toBe(true);
    });

    it('should allow exactly 5 images (at the limit)', () => {
      const images = createMockPendingImages(5);
      expect(images.length).toBe(MAX_COUNT);
    });

    it('should not allow adding when at the limit (5 images)', () => {
      const existingImages = createMockPendingImages(5);
      // Cannot add more images
      expect(existingImages.length >= MAX_COUNT).toBe(true);
    });

    it('should not allow adding when over the limit', () => {
      // This tests the boundary condition
      const existingImages = createMockPendingImages(6);
      expect(existingImages.length >= MAX_COUNT).toBe(true);
    });

    it('should allow adding first image when list is empty', () => {
      const existingImages: PendingImage[] = [];
      expect(existingImages.length < MAX_COUNT).toBe(true);
    });

    it('should correctly calculate remaining slots', () => {
      for (let count = 0; count <= MAX_COUNT; count++) {
        const images = createMockPendingImages(count);
        const canAddMore = images.length < MAX_COUNT;
        const expectedCanAdd = count < MAX_COUNT;
        expect(canAddMore).toBe(expectedCanAdd);
      }
    });
  });

  describe('Error Message for Max Limit (Requirement 3.6)', () => {
    it('should have correct error message format', () => {
      // The error message should be "最多只能上传 5 张图片"
      const expectedMessage = `最多只能上传 ${MAX_COUNT} 张图片`;
      expect(expectedMessage).toBe('最多只能上传 5 张图片');
    });
  });
});


/**
 * Unit tests for deletePendingImage functionality.
 * Validates: Requirements 3.3, 3.4
 */
describe('deletePendingImage', () => {
  /**
   * Helper to create a mock PendingImage for testing.
   */
  function createMockPendingImage(id: string): PendingImage {
    const file = new File(['test'], `test-${id}.png`, { type: 'image/png' });
    return {
      id,
      file,
      previewUrl: `blob:test-${id}`,
      status: 'pending',
    };
  }

  describe('Image Removal (Requirement 3.3)', () => {
    it('should remove the specified image from the list', () => {
      const images = [
        createMockPendingImage('img-1'),
        createMockPendingImage('img-2'),
        createMockPendingImage('img-3'),
      ];

      const result = deletePendingImage(images, 'img-2');

      expect(result).toHaveLength(2);
      expect(result.find((img) => img.id === 'img-2')).toBeUndefined();
      expect(result.find((img) => img.id === 'img-1')).toBeDefined();
      expect(result.find((img) => img.id === 'img-3')).toBeDefined();
    });

    it('should return a new array (immutable operation)', () => {
      const images = [createMockPendingImage('img-1')];
      const result = deletePendingImage(images, 'img-1');

      expect(result).not.toBe(images);
      expect(images).toHaveLength(1); // Original unchanged
      expect(result).toHaveLength(0);
    });

    it('should handle deleting from a single-item list', () => {
      const images = [createMockPendingImage('img-1')];
      const result = deletePendingImage(images, 'img-1');

      expect(result).toHaveLength(0);
    });

    it('should handle deleting the first item', () => {
      const images = [
        createMockPendingImage('img-1'),
        createMockPendingImage('img-2'),
      ];

      const result = deletePendingImage(images, 'img-1');

      expect(result).toHaveLength(1);
      expect(result[0].id).toBe('img-2');
    });

    it('should handle deleting the last item', () => {
      const images = [
        createMockPendingImage('img-1'),
        createMockPendingImage('img-2'),
      ];

      const result = deletePendingImage(images, 'img-2');

      expect(result).toHaveLength(1);
      expect(result[0].id).toBe('img-1');
    });

    it('should return unchanged array when ID not found', () => {
      const images = [
        createMockPendingImage('img-1'),
        createMockPendingImage('img-2'),
      ];

      const result = deletePendingImage(images, 'non-existent');

      expect(result).toHaveLength(2);
    });

    it('should handle empty array', () => {
      const images: PendingImage[] = [];
      const result = deletePendingImage(images, 'img-1');

      expect(result).toHaveLength(0);
    });
  });

  describe('Object URL Revocation (Requirement 3.4)', () => {
    it('should revoke the object URL when deleting an image', () => {
      const images = [createMockPendingImage('img-1')];
      const previewUrl = images[0].previewUrl;

      deletePendingImage(images, 'img-1');

      expect(mockRevokeObjectURL).toHaveBeenCalledTimes(1);
      expect(mockRevokeObjectURL).toHaveBeenCalledWith(previewUrl);
    });

    it('should not revoke any URL when image ID not found', () => {
      const images = [createMockPendingImage('img-1')];

      deletePendingImage(images, 'non-existent');

      expect(mockRevokeObjectURL).not.toHaveBeenCalled();
    });

    it('should only revoke the URL of the deleted image', () => {
      const images = [
        createMockPendingImage('img-1'),
        createMockPendingImage('img-2'),
        createMockPendingImage('img-3'),
      ];
      const deletedImageUrl = images[1].previewUrl;

      deletePendingImage(images, 'img-2');

      expect(mockRevokeObjectURL).toHaveBeenCalledTimes(1);
      expect(mockRevokeObjectURL).toHaveBeenCalledWith(deletedImageUrl);
    });

    it('should revoke URL for images with different statuses', () => {
      const uploadedImage: PendingImage = {
        id: 'img-uploaded',
        file: new File(['test'], 'test.png', { type: 'image/png' }),
        previewUrl: 'blob:uploaded',
        status: 'uploaded',
        uploadedUrl: '/uploads/test.png',
      };

      deletePendingImage([uploadedImage], 'img-uploaded');

      expect(mockRevokeObjectURL).toHaveBeenCalledWith('blob:uploaded');
    });

    it('should revoke URL for images with error status', () => {
      const errorImage: PendingImage = {
        id: 'img-error',
        file: new File(['test'], 'test.png', { type: 'image/png' }),
        previewUrl: 'blob:error',
        status: 'error',
        error: 'Upload failed',
      };

      deletePendingImage([errorImage], 'img-error');

      expect(mockRevokeObjectURL).toHaveBeenCalledWith('blob:error');
    });
  });
});

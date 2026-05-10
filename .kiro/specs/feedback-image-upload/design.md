# Technical Design Document: Feedback Image Upload

## Overview

This document describes the technical design for adding image upload capability to the existing feedback system. The feature enables users to attach screenshots and images when submitting bug reports or feature requests, improving the quality and clarity of feedback submissions.

### Goals

1. Allow users to paste images from clipboard (Ctrl+V) directly into the feedback form
2. Allow users to upload images via file picker (click to upload)
3. Provide image preview with thumbnail grid and full-size modal view
4. Support image deletion before submission
5. Store images on the server with content-hash-based deduplication
6. Display attached images when viewing feedback details

### Non-Goals

- Image editing or annotation within the application
- Drag-and-drop image upload (may be added later)
- Image compression or resizing on the client side
- Support for non-image file attachments

## Architecture

The feature follows the existing application architecture patterns:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Frontend (React)                            │
├─────────────────────────────────────────────────────────────────────┤
│  FeedbackPage.tsx                                                   │
│  ├── ImageUploader (new component)                                  │
│  │   ├── ClipboardHandler (paste event listener)                    │
│  │   ├── FilePickerButton (Ant Design Upload)                       │
│  │   └── ImagePreviewGrid (thumbnails + delete)                     │
│  └── FeedbackDetailModal (updated to show images)                   │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                │ HTTP API
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         Backend (FastAPI)                           │
├─────────────────────────────────────────────────────────────────────┤
│  POST /api/v1/feedbacks/images      → Upload image, return URL      │
│  POST /api/v1/feedbacks             → Create feedback with images   │
│  GET  /api/v1/feedbacks/{id}        → Get feedback with images      │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                │ File System
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Storage Layer                                  │
├─────────────────────────────────────────────────────────────────────┤
│  uploads/feedbacks/{content_hash}.{ext}  → Image files              │
│  PostgreSQL feedbacks.images             → JSON array of URLs       │
└─────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Image Addition**: User pastes (Ctrl+V) or selects image → Client validates size/type → Image added to local state as File object
2. **Feedback Submission**: User clicks submit → Client uploads all images to `/feedbacks/images` → Client receives URLs → Client submits feedback with image URLs
3. **Image Display**: User views feedback → Client fetches feedback with images array → Client renders image thumbnails → User clicks for full preview

## Components and Interfaces

### Frontend Components

#### ImageUploader Component

```typescript
interface ImageUploaderProps {
  images: PendingImage[];
  onChange: (images: PendingImage[]) => void;
  maxCount?: number;      // default: 5
  maxSizeMB?: number;     // default: 5
  disabled?: boolean;
}

interface PendingImage {
  id: string;             // client-generated UUID for tracking
  file: File;             // the actual file object
  previewUrl: string;     // object URL for preview
  status: 'pending' | 'uploading' | 'uploaded' | 'error';
  uploadedUrl?: string;   // server URL after upload
  error?: string;
}
```

**Responsibilities:**
- Listen for paste events on the feedback form container
- Extract image data from clipboard (DataTransfer API)
- Render file picker button using Ant Design Upload
- Validate image type (PNG, JPG, JPEG, GIF, WebP) and size (≤5MB)
- Manage local image state with preview URLs
- Display error messages for validation failures

#### ImagePreviewGrid Component

```typescript
interface ImagePreviewGridProps {
  images: PendingImage[];
  onDelete: (id: string) => void;
  onPreview: (image: PendingImage) => void;
}
```

**Responsibilities:**
- Render thumbnail grid (responsive, max 5 items)
- Show upload status indicator on each thumbnail
- Provide delete button overlay on hover
- Trigger full-size preview modal on click

#### FeedbackImageGallery Component

```typescript
interface FeedbackImageGalleryProps {
  images: string[];       // array of image URLs
}
```

**Responsibilities:**
- Render image thumbnails in feedback detail view
- Support click-to-preview with Ant Design Image.PreviewGroup

### Backend API

#### POST /api/v1/feedbacks/images

Upload a single feedback image.

**Request:**
- Content-Type: `multipart/form-data`
- Body: `file` (binary image data)

**Response:**
```json
{
  "url": "/uploads/feedbacks/a1b2c3d4e5f6.png",
  "filename": "screenshot.png"
}
```

**Validation:**
- Max file size: 5MB
- Allowed types: PNG, JPG, JPEG, GIF, WebP
- Authentication required

**Error Responses:**
- 400: Invalid file type
- 413: File too large
- 401: Unauthorized

#### Updated POST /api/v1/feedbacks

Create feedback with optional images.

**Request Body (updated):**
```json
{
  "type": "bug",
  "title": "Button not working",
  "description": "The submit button does not respond...",
  "images": [
    "/uploads/feedbacks/a1b2c3d4e5f6.png",
    "/uploads/feedbacks/b2c3d4e5f6a1.jpg"
  ]
}
```

#### Updated GET /api/v1/feedbacks/{id}

**Response (updated):**
```json
{
  "id": "uuid",
  "type": "bug",
  "title": "Button not working",
  "description": "...",
  "images": [
    "/uploads/feedbacks/a1b2c3d4e5f6.png"
  ],
  "status": "待AI分析",
  "created_at": "2024-01-15T10:30:00Z"
}
```

## Data Models

### Database Schema Update

```sql
-- Add images column to feedbacks table
ALTER TABLE feedbacks 
ADD COLUMN images JSONB DEFAULT '[]'::jsonb;

-- Create index for queries that filter by has-images
CREATE INDEX idx_feedbacks_has_images 
ON feedbacks ((images != '[]'::jsonb));
```

### SQLAlchemy Model Update

```python
# src/models/feedback.py
from sqlalchemy.dialects.postgresql import JSONB

class Feedback(Base, TimestampMixin):
    __tablename__ = "feedbacks"
    
    # ... existing fields ...
    
    images: Mapped[list[str]] = mapped_column(
        JSONB, 
        nullable=False, 
        default=list,
        server_default="[]"
    )
```

### Pydantic Schema Updates

```python
# src/schemas/feedback.py

class FeedbackCreate(BaseModel):
    type: str = Field(..., description="bug | feature")
    title: str
    description: str
    images: list[str] = Field(default_factory=list, max_length=5)

class FeedbackOut(BaseModel):
    # ... existing fields ...
    images: list[str] = Field(default_factory=list)

class FeedbackImageUploadResponse(BaseModel):
    url: str
    filename: str
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Image Count Invariant

*For any* feedback submission attempt, if the number of images exceeds 5, the system SHALL reject the submission and the feedback SHALL NOT be created.

**Validates: Requirements 3.5, 3.6**

### Property 2: Image Size Validation

*For any* image data with size greater than 5MB, the system SHALL reject the upload and return an appropriate error message, regardless of the image format or content.

**Validates: Requirements 1.4, 2.5**

### Property 3: Image Type Validation

*For any* file upload attempt, if the file extension is not in {png, jpg, jpeg, gif, webp}, the system SHALL reject the upload, regardless of the file's actual content.

**Validates: Requirements 2.3**

### Property 4: Image URL Persistence Round-Trip

*For any* successfully uploaded image, the returned URL SHALL be retrievable via HTTP GET and return the original image content unchanged.

**Validates: Requirements 5.2, 5.3**

### Property 5: Feedback-Image Association Integrity

*For any* feedback created with an images array, retrieving that feedback SHALL return the exact same images array in the same order.

**Validates: Requirements 5.4, 5.5, 6.1**

### Property 6: Clipboard Image Extraction

*For any* paste event containing image data in the clipboard, the system SHALL extract exactly one image and add it to the pending images list, preserving the original image data.

**Validates: Requirements 1.1, 1.2**

### Property 7: Non-Image Paste Passthrough

*For any* paste event where the clipboard does not contain image data, the system SHALL not modify the pending images list and SHALL not display any error.

**Validates: Requirements 1.3**

### Property 8: Image Deletion Consistency

*For any* pending image that is deleted via the delete button, the image SHALL be removed from the pending list and its preview URL SHALL be revoked.

**Validates: Requirements 3.3, 3.4**

## Error Handling

### Frontend Error Handling

| Error Condition | User Message | Recovery Action |
|----------------|--------------|-----------------|
| Image > 5MB | "图片大小不能超过 5MB" | Allow user to select different image |
| Invalid image type | "仅支持 PNG、JPG、JPEG、GIF、WebP 格式" | Allow user to select different image |
| Max images reached | "最多只能上传 5 张图片" | User must delete existing image first |
| Upload failed | "图片上传失败，请重试" | Retry button on failed image |
| Network error | "网络错误，请检查连接" | Retry submission |

### Backend Error Handling

| HTTP Status | Condition | Response Body |
|-------------|-----------|---------------|
| 400 | Invalid file type | `{"detail": "Unsupported image type: .pdf"}` |
| 413 | File too large | `{"detail": "Image too large (max 5MB)"}` |
| 422 | Too many images | `{"detail": "Maximum 5 images allowed"}` |
| 500 | Storage failure | `{"detail": "Failed to save image"}` |

### Error Recovery Strategy

1. **Partial Upload Failure**: If some images upload successfully but others fail, the user can retry failed images individually without re-uploading successful ones.

2. **Submission Failure**: If feedback submission fails after images are uploaded, the uploaded images remain on the server (orphaned). A background cleanup job can remove orphaned images older than 24 hours.

3. **Storage Full**: If disk storage is full, return 507 Insufficient Storage and alert system administrators.

## Testing Strategy

### Unit Tests

**Frontend:**
- ImageUploader component renders correctly
- Paste event handler extracts image from clipboard
- File picker filters by allowed types
- Size validation rejects files > 5MB
- Delete button removes image from list
- Preview modal displays correct image

**Backend:**
- Image upload endpoint validates file type
- Image upload endpoint validates file size
- Content hash generates unique filenames
- Feedback creation accepts images array
- Feedback retrieval includes images

### Property-Based Tests

Property-based tests will be implemented using `fast-check` (frontend) and `hypothesis` (backend) with minimum 100 iterations per property.

**Frontend Properties (fast-check):**
- Property 6: Clipboard image extraction
- Property 7: Non-image paste passthrough
- Property 8: Image deletion consistency

**Backend Properties (hypothesis):**
- Property 1: Image count invariant
- Property 2: Image size validation
- Property 3: Image type validation
- Property 4: Image URL persistence round-trip
- Property 5: Feedback-image association integrity

### Integration Tests

1. **End-to-end upload flow**: Paste image → Preview appears → Submit feedback → Image visible in detail view
2. **Multiple images**: Add 5 images via mixed paste/upload → Submit → All images preserved
3. **Error recovery**: Upload fails → Retry → Success → Submit works

### Manual Testing Checklist

- [ ] Paste screenshot from Windows Snipping Tool
- [ ] Paste image copied from browser
- [ ] Upload via file picker on Windows/Mac
- [ ] Preview modal shows full-size image
- [ ] Delete removes image immediately
- [ ] Submit with 0, 1, 3, 5 images
- [ ] Attempt to add 6th image shows error
- [ ] View feedback detail shows all images
- [ ] Click thumbnail opens preview

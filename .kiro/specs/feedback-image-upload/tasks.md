# Implementation Plan: Feedback Image Upload

## Overview

This implementation plan adds image upload capability to the existing feedback system. Users can paste screenshots (Ctrl+V) or click an upload button to add images when submitting feedback. The implementation follows the existing architecture patterns with React/TypeScript frontend and FastAPI/Python backend.

## Tasks

- [x] 1. Backend: Database schema and model updates
  - [x] 1.1 Create Alembic migration to add images column to feedbacks table
    - Add `images JSONB DEFAULT '[]'::jsonb` column to feedbacks table
    - Create index for queries filtering by has-images
    - _Requirements: 5.4_

  - [x] 1.2 Update Feedback SQLAlchemy model with images field
    - Add `images: Mapped[list[str]]` field with JSONB type
    - Set default to empty list with server_default
    - _Requirements: 5.4_

  - [x] 1.3 Update Pydantic schemas for feedback with images support
    - Add `images: list[str]` field to `FeedbackCreate` with max_length=5 validation
    - Add `images: list[str]` field to `FeedbackOut`
    - Create `FeedbackImageUploadResponse` schema with url and filename fields
    - _Requirements: 5.4, 5.5_

- [x] 2. Backend: Image upload API endpoint
  - [x] 2.1 Create image upload endpoint POST /api/v1/feedbacks/images
    - Accept multipart/form-data with file field
    - Validate file type (PNG, JPG, JPEG, GIF, WebP)
    - Validate file size (max 5MB)
    - Generate content hash for filename
    - Save to uploads/feedbacks directory
    - Return URL and original filename
    - _Requirements: 5.1, 5.2, 5.3_

  - [ ]* 2.2 Write property test for image size validation (Property 2)
    - **Property 2: Image Size Validation**
    - Test that any image > 5MB is rejected regardless of format
    - **Validates: Requirements 1.4, 2.5**

  - [ ]* 2.3 Write property test for image type validation (Property 3)
    - **Property 3: Image Type Validation**
    - Test that files with invalid extensions are rejected
    - **Validates: Requirements 2.3**

  - [x] 2.4 Update feedback creation endpoint to accept images array
    - Modify POST /api/v1/feedbacks to accept images field
    - Validate images array length (max 5)
    - Store images array in database
    - _Requirements: 5.5_

  - [ ]* 2.5 Write property test for image count invariant (Property 1)
    - **Property 1: Image Count Invariant**
    - Test that feedback with > 5 images is rejected
    - **Validates: Requirements 3.5, 3.6**

  - [x] 2.6 Update feedback retrieval endpoint to return images
    - Modify GET /api/v1/feedbacks/{id} to include images array
    - Modify GET /api/v1/feedbacks list endpoint to include images
    - _Requirements: 6.1_

  - [ ]* 2.7 Write property test for feedback-image association integrity (Property 5)
    - **Property 5: Feedback-Image Association Integrity**
    - Test that created feedback returns exact same images array
    - **Validates: Requirements 5.4, 5.5, 6.1**

- [x] 3. Checkpoint - Backend API complete
  - Ensure all backend tests pass, ask the user if questions arise.

- [x] 4. Frontend: ImageUploader component
  - [x] 4.1 Create PendingImage interface and ImageUploader component structure
    - Define PendingImage interface with id, file, previewUrl, status, uploadedUrl, error
    - Create ImageUploader component with props for images, onChange, maxCount, maxSizeMB, disabled
    - Set up component state management
    - _Requirements: 1.1, 2.1_

  - [x] 4.2 Implement clipboard paste handler for image capture
    - Add paste event listener to feedback form container
    - Extract image data from clipboard using DataTransfer API
    - Create File object from clipboard image data
    - Add image to pending list with preview URL
    - _Requirements: 1.1, 1.2, 1.3_

  - [ ]* 4.3 Write property test for clipboard image extraction (Property 6)
    - **Property 6: Clipboard Image Extraction**
    - Test that paste events with image data extract exactly one image
    - **Validates: Requirements 1.1, 1.2**

  - [ ]* 4.4 Write property test for non-image paste passthrough (Property 7)
    - **Property 7: Non-Image Paste Passthrough**
    - Test that paste events without image data don't modify pending list
    - **Validates: Requirements 1.3**

  - [x] 4.5 Implement file picker button using Ant Design Upload
    - Add Upload button with accept filter for allowed image types
    - Handle file selection and add to pending images
    - Validate file type and size before adding
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 4.6 Implement image validation (type and size)
    - Validate image type against allowed formats (PNG, JPG, JPEG, GIF, WebP)
    - Validate image size against 5MB limit
    - Display appropriate error messages for validation failures
    - _Requirements: 1.4, 2.3, 2.5_

- [x] 5. Frontend: ImagePreviewGrid component
  - [x] 5.1 Create ImagePreviewGrid component with thumbnail display
    - Render responsive grid of image thumbnails
    - Show upload status indicator on each thumbnail
    - Display error state for failed uploads
    - _Requirements: 3.1_

  - [x] 5.2 Implement delete functionality for pending images
    - Add delete button overlay on thumbnail hover
    - Remove image from pending list on delete
    - Revoke object URL to prevent memory leaks
    - _Requirements: 3.3, 3.4_

  - [ ]* 5.3 Write property test for image deletion consistency (Property 8)
    - **Property 8: Image Deletion Consistency**
    - Test that deleted images are removed and preview URLs revoked
    - **Validates: Requirements 3.3, 3.4**

  - [x] 5.4 Implement full-size image preview modal
    - Add click handler to open preview modal
    - Use Ant Design Image.PreviewGroup for modal display
    - _Requirements: 3.2_

  - [x] 5.5 Implement max images limit enforcement
    - Check pending images count before adding new image
    - Display error message when limit (5) is reached
    - _Requirements: 3.5, 3.6_

- [x] 6. Frontend: Integrate ImageUploader with FeedbackPage
  - [x] 6.1 Add ImageUploader to feedback form in FeedbackPage
    - Import and render ImageUploader component
    - Manage pending images state in FeedbackPage
    - Pass images and onChange handler to ImageUploader
    - _Requirements: 1.1, 2.1_

  - [x] 6.2 Implement image upload on feedback submission
    - Upload all pending images to /api/v1/feedbacks/images before submission
    - Collect uploaded URLs from responses
    - Handle upload progress indication
    - _Requirements: 4.1, 4.4_

  - [x] 6.3 Submit feedback with image URLs
    - Include images array in feedback creation request
    - Handle submission errors and display messages
    - Clear form and images on successful submission
    - _Requirements: 4.2, 4.3, 4.5_

  - [ ]* 6.4 Write unit tests for feedback submission with images
    - Test successful submission with 0, 1, 3, 5 images
    - Test error handling for upload failures
    - _Requirements: 4.1, 4.2, 4.3_

- [x] 7. Checkpoint - Image upload flow complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Frontend: FeedbackImageGallery for detail view
  - [x] 8.1 Create FeedbackImageGallery component
    - Accept images array prop (list of URLs)
    - Render thumbnail grid for feedback images
    - Use Ant Design Image.PreviewGroup for click-to-preview
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 8.2 Integrate FeedbackImageGallery into FeedbackModal/detail view
    - Import and render FeedbackImageGallery in feedback detail
    - Conditionally render only when images array is non-empty
    - _Requirements: 6.1, 6.4_

  - [ ]* 8.3 Write unit tests for FeedbackImageGallery
    - Test rendering with 0, 1, 5 images
    - Test preview modal opens on click
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [x] 9. Backend: Static file serving for uploaded images
  - [x] 9.1 Configure FastAPI to serve uploaded images
    - Mount static files route for /uploads/feedbacks
    - Ensure uploads directory exists on startup
    - _Requirements: 5.2_

  - [ ]* 9.2 Write property test for image URL persistence round-trip (Property 4)
    - **Property 4: Image URL Persistence Round-Trip**
    - Test that uploaded images are retrievable via returned URL
    - **Validates: Requirements 5.2, 5.3**

- [x] 10. Final checkpoint - Feature complete
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- Frontend uses TypeScript with React and Ant Design
- Backend uses Python with FastAPI, SQLAlchemy, and Pydantic
- Images are stored on local filesystem with content-hash-based deduplication

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["2.1", "4.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "2.4", "4.2", "4.5", "4.6"] },
    { "id": 4, "tasks": ["2.5", "2.6", "4.3", "4.4", "5.1"] },
    { "id": 5, "tasks": ["2.7", "5.2", "5.4", "5.5"] },
    { "id": 6, "tasks": ["5.3", "6.1"] },
    { "id": 7, "tasks": ["6.2", "8.1", "9.1"] },
    { "id": 8, "tasks": ["6.3", "6.4", "8.2", "9.2"] },
    { "id": 9, "tasks": ["8.3"] }
  ]
}
```

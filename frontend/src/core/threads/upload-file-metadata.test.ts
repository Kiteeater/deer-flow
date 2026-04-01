import assert from "node:assert/strict";
import test from "node:test";

import type { UploadedFileInfo } from "../uploads/api";

const { buildMessageFilesFromUploadInfo } = await import(
  new URL("./upload-file-metadata.ts", import.meta.url).href,
);

void test("preserves extracted_images when mapping upload info into message metadata", () => {
  const uploadedFiles: UploadedFileInfo[] = [
    {
      filename: "report.docx",
      size: 42,
      path: "/tmp/report.docx",
      virtual_path: "/mnt/user-data/uploads/report.docx",
      artifact_url: "/api/threads/t1/artifacts/mnt/user-data/uploads/report.docx",
      extracted_images: [
        {
          filename: "report__image1.png",
          size: 3,
          path: "/tmp/report__image1.png",
          virtual_path: "/mnt/user-data/uploads/report__image1.png",
          artifact_url: "/api/threads/t1/artifacts/mnt/user-data/uploads/report__image1.png",
        },
      ],
    },
  ];

  assert.deepEqual(buildMessageFilesFromUploadInfo(uploadedFiles), [
    {
      filename: "report.docx",
      size: 42,
      path: "/mnt/user-data/uploads/report.docx",
      status: "uploaded",
      extracted_images: [
        {
          filename: "report__image1.png",
          size: 3,
          path: "/mnt/user-data/uploads/report__image1.png",
          virtual_path: "/mnt/user-data/uploads/report__image1.png",
          artifact_url: "/api/threads/t1/artifacts/mnt/user-data/uploads/report__image1.png",
        },
      ],
    },
  ]);
});

void test("preserves markdown companion metadata when mapping upload info into message metadata", () => {
  const uploadedFiles: UploadedFileInfo[] = [
    {
      filename: "report.docx",
      size: 42,
      path: "/tmp/report.docx",
      virtual_path: "/mnt/user-data/uploads/report.docx",
      artifact_url: "/api/threads/t1/artifacts/mnt/user-data/uploads/report.docx",
      markdown_file: "report.md",
      markdown_path: "/tmp/report.md",
      markdown_virtual_path: "/mnt/user-data/uploads/report.md",
      markdown_artifact_url: "/api/threads/t1/artifacts/mnt/user-data/uploads/report.md",
    },
  ];

  assert.deepEqual(buildMessageFilesFromUploadInfo(uploadedFiles), [
    {
      filename: "report.docx",
      size: 42,
      path: "/mnt/user-data/uploads/report.docx",
      status: "uploaded",
      markdown_file: "report.md",
      markdown_path: "/mnt/user-data/uploads/report.md",
    },
  ]);
});

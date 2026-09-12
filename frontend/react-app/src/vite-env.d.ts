/// <reference types="vite/client" />

// `?url` imports (ใช้ชี้ worker ของ pdfjs) -- Vite แปลงเป็น URL ของไฟล์ที่ build ออกมา
declare module '*?url' {
  const src: string;
  export default src;
}

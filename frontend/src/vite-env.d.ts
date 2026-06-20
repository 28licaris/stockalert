/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AUTH_MODE?: "dev" | "preview" | "session";
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

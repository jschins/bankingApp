/** Build-time app mode. Set ``VITE_APP_MODE=central_admin`` when bundling centraleAdmin.exe. */
export const IS_CENTRAL_ADMIN =
  String(import.meta.env.VITE_APP_MODE || "").toLowerCase() === "central_admin";

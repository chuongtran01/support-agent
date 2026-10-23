import type { Http } from "./http";
import type { LoginResponse, Profile } from "./types";

export const authApi = ({ json, post }: Http) => ({
  login: (email: string, password: string) =>
    json<LoginResponse>("/auth/login", post({ email, password })),
  me: () => json<Profile>("/me"),
});

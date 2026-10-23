import { redirect } from "next/navigation";

// The chat page sends anyone who is not signed in to /login.
export default function Home() {
  redirect("/chat");
}

import NextAuth from "next-auth";

// Staff sign-in. The session carries the staff member's id and role.
export const { auth, handlers, signIn, signOut } = NextAuth({
  providers: [],
  callbacks: {
    session({ session, token }) {
      return { ...session, user: { ...session.user, id: token.sub, role: token.role as string } };
    },
  },
});

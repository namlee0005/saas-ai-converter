import { clerkMiddleware, type ClerkMiddlewareAuth } from "@clerk/nextjs/server";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Next.js 16 "proxy" convention (replaces deprecated middleware.ts).
 * Clerk's clerkMiddleware wraps the handler and returns a NextMiddleware-
 * compatible function, which the proxy convention accepts.
 */
export const proxy = clerkMiddleware(
  async (auth: ClerkMiddlewareAuth, request: NextRequest) => {
    const { userId } = await auth();

    // Allow unauthenticated access to public routes
    const publicPaths = ["/", "/sign-in", "/sign-up", "/api/webhooks"];
    const isPublic = publicPaths.some((path) =>
      request.nextUrl.pathname.startsWith(path),
    );

    if (!userId && !isPublic) {
      const signInUrl = new URL("/sign-in", request.url);
      signInUrl.searchParams.set("redirect_url", request.nextUrl.pathname);
      return NextResponse.redirect(signInUrl);
    }

    return NextResponse.next();
  },
);

export const config = {
  matcher: [
    // Skip Next.js internals and static files
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes
    "/(api|trpc)(.*)",
  ],
};

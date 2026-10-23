"use client";

import { Loader2 } from "lucide-react";

import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker";
import { Message, MessageContent } from "@/components/ui/message";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { SourceChip } from "@/components/source-chip";
import { answerParts } from "@/chat/answer";
import type { ChatError, ChatMessage, LiveTurn } from "@/chat/reducer";
import { toolStatus } from "@/chat/tools";

function Row({ own, children }: { own: boolean; children: React.ReactNode }) {
  return (
    <Message align={own ? "end" : "start"}>
      <MessageContent>
        <Bubble variant={own ? "default" : "muted"}>
          <BubbleContent className="whitespace-pre-wrap">
            {children}
          </BubbleContent>
        </Bubble>
      </MessageContent>
    </Message>
  );
}

function Answer({ message }: { message: ChatMessage }) {
  return (
    <Row own={false}>
      {answerParts(message.text, message.citations).map((part, i) =>
        part.kind === "text" ? (
          <span key={i}>{part.text}</span>
        ) : (
          <SourceChip key={i} number={part.number} citation={part.citation} />
        ),
      )}
    </Row>
  );
}

export function Thread({
  messages,
  turn,
  error,
  loading,
  onNewChat,
}: {
  messages: ChatMessage[];
  turn: LiveTurn | null;
  error: ChatError | null;
  loading: boolean;
  onNewChat: () => void;
}) {
  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="last-anchor">
      <MessageScroller className="flex-1">
        <MessageScrollerViewport>
          {/* Announce a row when it is added, not every word of it: wait while a turn streams. */}
          <MessageScrollerContent
            aria-busy={turn !== null}
            className="mx-auto max-w-2xl gap-3 p-4"
          >
            {loading && (
              <MessageScrollerItem messageId="loading">
                <Marker role="status">
                  <MarkerIcon>
                    <Loader2 className="animate-spin motion-reduce:animate-none" />
                  </MarkerIcon>
                  <MarkerContent>Loading chat…</MarkerContent>
                </Marker>
              </MessageScrollerItem>
            )}
            {messages.length === 0 && !turn && !loading && (
              <MessageScrollerItem messageId="empty">
                <p className="py-16 text-center text-muted-foreground">
                  Ask about an order, a return, shipping or anything else.
                </p>
              </MessageScrollerItem>
            )}
            {messages.map((message) => (
              <MessageScrollerItem
                key={message.key}
                messageId={message.key}
                scrollAnchor={message.role === "user"}
              >
                {message.role === "user" ? (
                  <Row own>{message.text}</Row>
                ) : (
                  <Answer message={message} />
                )}
              </MessageScrollerItem>
            ))}
            {turn && (
              <MessageScrollerItem messageId="live">
                {turn.text ? (
                  <Row own={false}>{turn.text}</Row>
                ) : (
                  <Marker role="status">
                    <MarkerIcon>
                      <Loader2 className="animate-spin motion-reduce:animate-none" />
                    </MarkerIcon>
                    <MarkerContent>
                      {turn.tool ? toolStatus(turn.tool) : "Thinking…"}
                    </MarkerContent>
                  </Marker>
                )}
              </MessageScrollerItem>
            )}
            {error && (
              <MessageScrollerItem messageId="error">
                <Message>
                  <MessageContent>
                    <Bubble
                      variant="destructive"
                      role="alert"
                      className="max-w-full"
                    >
                      <BubbleContent className="flex flex-wrap items-center gap-3">
                        <span>{error.message}</span>
                        {error.code === "chat_full" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={onNewChat}
                          >
                            Start a new chat
                          </Button>
                        )}
                      </BubbleContent>
                    </Bubble>
                  </MessageContent>
                </Message>
              </MessageScrollerItem>
            )}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  );
}

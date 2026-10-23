"use client";

import Link from "next/link";

import type { Order, OrderItem } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "cn";

import { useLoad } from "@/lib/use-load";
import { formatCents } from "@/lib/money";
import {
  askAboutOrder,
  askAboutReturn,
  canAskAboutReturn,
  chatWith,
  orderStatusLabel,
  refundStatus,
} from "@/orders/labels";

const day = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  year: "numeric",
});

function ItemRow({ order, item }: { order: Order; item: OrderItem }) {
  const refunds = order.refunds.filter(
    (r) => r.order_item_id === item.order_item_id,
  );
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3">
      <div className="min-w-0">
        <p className="font-medium">
          {item.name}
          {item.quantity > 1 && (
            <span className="text-muted-foreground"> × {item.quantity}</span>
          )}
        </p>
        <p className="text-sm text-muted-foreground">
          {formatCents(item.price_cents)}
          {item.quantity > 1 && " each"}
        </p>
        {(refunds.length > 0 || item.is_final_sale) && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {item.is_final_sale && <Badge variant="outline">Final sale</Badge>}
            {refunds.map((refund) => {
              const { label, tone } = refundStatus(refund.status);
              return (
                <Badge key={refund.refund_id} variant={tone}>
                  {label} {formatCents(refund.amount_cents)}
                </Badge>
              );
            })}
          </div>
        )}
      </div>
      {canAskAboutReturn(order, item) && (
        <Link
          href={chatWith(askAboutReturn(order, item))}
          className={cn(buttonVariants({ size: "sm", variant: "outline" }))}
        >
          Ask about a return
          <span className="sr-only">
            {" "}
            for {item.name} from order {order.id}
          </span>
        </Link>
      )}
    </li>
  );
}

function OrderCard({ order }: { order: Order }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">Order #{order.id}</CardTitle>
            <CardDescription>
              Placed {day.format(new Date(order.placed_at))} ·{" "}
              {formatCents(order.total_cents)}
            </CardDescription>
          </div>
          <Badge
            variant={order.status === "cancelled" ? "destructive" : "secondary"}
          >
            {orderStatusLabel(order.status)}
            {order.delivered_at &&
              ` ${day.format(new Date(order.delivered_at))}`}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <ul className="divide-y">
          {order.items.map((item) => (
            <ItemRow key={item.order_item_id} order={order} item={item} />
          ))}
        </ul>
      </CardContent>
      <CardFooter>
        <Link
          href={chatWith(askAboutOrder(order))}
          className={cn(buttonVariants({ size: "sm", variant: "ghost" }))}
        >
          Ask about this order
          <span className="sr-only"> #{order.id}</span>
        </Link>
      </CardFooter>
    </Card>
  );
}

export default function OrdersPage() {
  const {
    data: orders,
    error,
    loading,
    reload,
  } = useLoad((api) => api.orders());

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-4 pt-8">
      <div>
        <h1 className="text-xl font-semibold">Your orders</h1>
        <p className="text-sm text-muted-foreground">
          Pick one to ask the support agent about it.
        </p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center gap-3">
            <span>{error}</span>
            <Button size="sm" variant="outline" onClick={reload}>
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {loading && (
        <div
          className="flex flex-col gap-3"
          role="status"
          aria-label="Loading orders"
        >
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      )}

      {orders?.length === 0 && (
        <p className="py-10 text-center text-muted-foreground">
          You have no orders yet.
        </p>
      )}

      {orders?.map((order) => (
        <OrderCard key={order.id} order={order} />
      ))}
    </div>
  );
}

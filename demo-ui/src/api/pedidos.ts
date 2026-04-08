import { urlPedidos } from "../lib/env";
import { getJson, patchJson, postJson, putJson } from "../lib/http";
import { asRecord, strField } from "../lib/record";

export type OrderStatus =
  | "CONFIRMED"
  | "PREPARING"
  | "READY_FOR_PICKUP"
  | "PICKED_UP"
  | "IN_TRANSIT"
  | "DELIVERED";

export type OrderItem = Record<string, unknown>;

export type OrderDTO = {
  order_id: string;
  customer_id: string;
  restaurant_id: string;
  status: OrderStatus;
  items: OrderItem[];
  total_value: string;
  entregador_id?: string;
  created_at?: string;
  updated_at?: string;
};

export type DriverLocationDTO = {
  driver_id: string;
  lat: string;
  lng: string;
  order_id?: string;
  updated_at?: string;
};

function mapOrder(data: Record<string, unknown>): OrderDTO {
  const itemsRaw = data.items ?? data.ITEMS;
  const items = Array.isArray(itemsRaw) ? (itemsRaw as OrderItem[]) : [];
  const status = strField(data, "status", "STATUS") as OrderStatus;
  return {
    order_id: strField(data, "order_id", "ORDER_ID"),
    customer_id: strField(data, "customer_id", "CUSTOMER_ID"),
    restaurant_id: strField(data, "restaurant_id", "RESTAURANT_ID"),
    status: status || "CONFIRMED",
    items,
    total_value: strField(data, "total_value", "TOTAL_VALUE"),
    entregador_id: strField(data, "entregador_id", "ENTREGADOR_ID") || undefined,
    created_at: strField(data, "created_at", "CREATED_AT") || undefined,
    updated_at: strField(data, "updated_at", "UPDATED_AT") || undefined,
  };
}

function mapDriverLoc(data: Record<string, unknown>): DriverLocationDTO {
  return {
    driver_id: strField(data, "driver_id", "DRIVER_ID"),
    lat: strField(data, "lat", "LAT"),
    lng: strField(data, "lng", "LNG"),
    order_id: strField(data, "order_id", "ORDER_ID") || undefined,
    updated_at: strField(data, "updated_at", "UPDATED_AT") || undefined,
  };
}

export async function createOrder(body: {
  customer_id: string;
  restaurant_id: string;
  items: OrderItem[];
  total_value: number;
}): Promise<OrderDTO> {
  const data = await postJson(urlPedidos("/orders"), body);
  return mapOrder(asRecord(data));
}

export async function fetchOrder(orderId: string): Promise<OrderDTO> {
  const data = await getJson(urlPedidos(`/orders/${encodeURIComponent(orderId)}`));
  return mapOrder(asRecord(data));
}

export async function patchOrderStatus(
  orderId: string,
  body: { status: OrderStatus; entregador_id?: string }
): Promise<void> {
  await patchJson(urlPedidos(`/orders/${encodeURIComponent(orderId)}/status`), body);
}

export async function putDriverLocation(
  driverId: string,
  body: { lat: number; lng: number; order_id?: string }
): Promise<void> {
  await putJson(urlPedidos(`/drivers/${encodeURIComponent(driverId)}/location`), body);
}

export async function fetchDriverLocation(driverId: string): Promise<DriverLocationDTO> {
  const data = await getJson(urlPedidos(`/drivers/${encodeURIComponent(driverId)}/location`));
  return mapDriverLoc(asRecord(data));
}

export async function fetchOrderHistory(orderId: string): Promise<unknown[]> {
  const data = await getJson(urlPedidos(`/orders/${encodeURIComponent(orderId)}/history`));
  return Array.isArray(data) ? data : [];
}

export async function fetchOrdersByCustomer(customerId: string): Promise<OrderDTO[]> {
  const data = await getJson(urlPedidos(`/orders/customer/${encodeURIComponent(customerId)}`));
  if (!Array.isArray(data)) return [];
  return data.map((o) => mapOrder(asRecord(o)));
}

export async function fetchOrdersByStatus(status: OrderStatus): Promise<OrderDTO[]> {
  const data = await getJson(urlPedidos(`/orders/status/${encodeURIComponent(status)}`));
  if (!Array.isArray(data)) return [];
  return data.map((o) => mapOrder(asRecord(o)));
}

import L from "leaflet";
import { useEffect } from "react";
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap } from "react-leaflet";

function makeIcon(emoji: string, color: string) {
  return new L.DivIcon({
    html: `
      <div style="
        background-color: ${color};
        width: 36px;
        height: 36px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 20px;
        border: 2px solid white;
        box-shadow: 0 2px 4px rgba(0,0,0,0.3);
      ">
        ${emoji}
      </div>
    `,
    className: "",
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -18],
  });
}

const ICON_DRIVER = makeIcon("🛵", "#f97316"); // laranja
const ICON_RESTAURANT = makeIcon("🍕", "#3b82f6"); // azul
const ICON_CUSTOMER = makeIcon("🏠", "#22c55e"); // verde

type LatLng = { lat: number; lng: number };

function Recenter({ center }: { center: LatLng }) {
  const map = useMap();
  useEffect(() => {
    map.setView([center.lat, center.lng], map.getZoom(), { animate: true });
  }, [center.lat, center.lng, map]);
  return null;
}

type Props = {
  driver: LatLng | null;
  restaurant?: LatLng | null;
  customer?: LatLng | null;
  driverName?: string;
  restaurantName?: string;
  routePoints?: LatLng[];
};

export function DriverMap({ driver, restaurant, customer, driverName, restaurantName, routePoints }: Props) {
  const center: LatLng = driver ?? restaurant ?? customer ?? { lat: -23.5505, lng: -46.6333 };

  return (
    <div style={{ height: 400, borderRadius: 12, overflow: "hidden", border: "1px solid var(--border)", marginTop: "1rem" }}>
      <MapContainer
        center={[center.lat, center.lng]}
        zoom={14}
        style={{ height: "100%", width: "100%" }}
        scrollWheelZoom={false}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        {routePoints && routePoints.length > 0 && (
          <Polyline
            positions={routePoints.map(p => [p.lat, p.lng])}
            color="#3b82f6"
            weight={5}
            opacity={0.6}
            dashArray="10, 10"
          />
        )}

        {driver && (
          <>
            <Recenter center={driver} />
            <Marker position={[driver.lat, driver.lng]} icon={ICON_DRIVER}>
              <Popup>{driverName ?? "Entregador"}</Popup>
            </Marker>
          </>
        )}

        {restaurant && (
          <Marker position={[restaurant.lat, restaurant.lng]} icon={ICON_RESTAURANT}>
            <Popup>{restaurantName ?? "Restaurante"}</Popup>
          </Marker>
        )}

        {customer && (
          <Marker position={[customer.lat, customer.lng]} icon={ICON_CUSTOMER}>
            <Popup>Destino (Cliente)</Popup>
          </Marker>
        )}
      </MapContainer>
    </div>
  );
}

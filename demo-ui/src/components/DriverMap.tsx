import { useEffect } from "react";
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet";
import L from "leaflet";

function pinSvg(color: string) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="36" viewBox="0 0 28 36"><path d="M14 0C6.3 0 0 6.3 0 14c0 9.6 14 22 14 22S28 23.6 28 14C28 6.3 21.7 0 14 0z" fill="${color}" stroke="#fff" stroke-width="1.5"/><circle cx="14" cy="14" r="6" fill="#fff"/></svg>`;
}

function makePin(color: string) {
  return new L.DivIcon({
    html: pinSvg(color),
    className: "",
    iconSize: [28, 36],
    iconAnchor: [14, 36],
    popupAnchor: [0, -38],
  });
}

const ICON_DRIVER     = makePin("#c45c26"); // laranja — entregador
const ICON_RESTAURANT = makePin("#2563eb"); // azul   — restaurante (origem)
const ICON_CUSTOMER   = makePin("#16a34a"); // verde  — destino (cliente)

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
};

export function DriverMap({ driver, restaurant, customer, driverName, restaurantName }: Props) {
  const center: LatLng = driver ?? restaurant ?? customer ?? { lat: -23.5505, lng: -46.6333 };

  return (
    <div style={{ height: 320, borderRadius: 6, overflow: "hidden", border: "1px solid var(--border)" }}>
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

        {driver && (
          <>
            <Recenter center={driver} />
            <Marker position={[driver.lat, driver.lng]} icon={ICON_DRIVER}>
              <Popup>{driverName ?? "Entregador"} 🛵</Popup>
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
            <Popup>Destino</Popup>
          </Marker>
        )}
      </MapContainer>
    </div>
  );
}

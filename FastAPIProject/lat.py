from geopy.geocoders import Nominatim

def get_lat_long(address):
    geolocator = Nominatim(user_agent="myGeocoder")  # User agent is required
    location = geolocator.geocode(address)
    if location:
        return location.latitude, location.longitude
    return None

address = " ж.к. Разсадника-Коньовица 22А Г ,София"
lat_long = get_lat_long(address)

if lat_long:
    print(f"Latitude: {lat_long[0]}, Longitude: {lat_long[1]}")
else:
    print("Address not found.")

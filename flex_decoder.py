import re
import binascii


class FlexProtocolParser:
    def __init__(self):
        # Protocol versions mapping
        self.protocol_versions = {
            0x0A: "1.0",
            0x14: "2.0",
            0x1E: "3.0"
        }

        # Structure versions sizes
        self.struct_sizes = {
            "1.0": 69,
            "2.0": 122,
            "3.0": 255
        }

        # Bitfield sizes in bytes
        self.bitfield_sizes = {
            "1.0": 9,
            "2.0": 16,
            "3.0": 32
        }

    def parse_hex(self, hex_string):
        # Remove any spaces or other non-hex characters
        hex_string = re.sub(r'[^0-9a-fA-F]', '', hex_string)

        # Convert hex string to bytes
        data = binascii.unhexlify(hex_string)

        # Parse the data
        return self.parse_data(data)

    def parse_data(self, data):
        # Check minimum length for the header
        if len(data) < 16:
            return {"error": "Data too short for NTCB header"}

        # Extract NTCB header
        ntcb_header = data[:16]
        offset = 16

        # Check for *>FLEX or *<FLEX marker
        if offset + 6 > len(data):
            return {"error": "Data too short for FLEX marker"}

        try:
            flex_marker = data[offset:offset + 6].decode('ascii', errors='replace')
        except:
            return {"error": "Invalid FLEX marker"}

        offset += 6

        # Determine direction based on marker
        direction = "outgoing" if flex_marker == "*>FLEX" else "incoming" if flex_marker == "*<FLEX" else "unknown"

        if direction == "unknown":
            return {"error": f"Unknown FLEX marker: {flex_marker}"}

        # Parse protocol byte
        if offset + 1 > len(data):
            return {"error": "Data too short for protocol byte"}

        protocol = data[offset]
        offset += 1

        if protocol != 0xB0:  # 0xB0 = FLEX
            return {"error": f"Not a FLEX protocol (got 0x{protocol:02X}, expected 0xD0)"}

        # Parse protocol version
        if offset + 1 > len(data):
            return {"error": "Data too short for protocol version"}

        protocol_version_byte = data[offset]
        offset += 1

        protocol_version = self.protocol_versions.get(protocol_version_byte, f"Unknown (0x{protocol_version_byte:02X})")

        # Parse structure version
        if offset + 1 > len(data):
            return {"error": "Data too short for structure version"}

        struct_version_byte = data[offset]
        offset += 1

        struct_version = self.protocol_versions.get(struct_version_byte, f"Unknown (0x{struct_version_byte:02X})")

        # Parse data size
        if offset + 1 > len(data):
            return {"error": "Data too short for data size"}

        data_size = data[offset]
        offset += 1

        # Get the appropriate bitfield size based on the structure version
        bitfield_size = self.bitfield_sizes.get(struct_version, 0)

        # Parse bitfield mask
        if offset + bitfield_size > len(data):
            return {"error": f"Data too short for bitfield (need {bitfield_size} bytes)"}

        bitfield = data[offset:offset + bitfield_size]
        offset += bitfield_size

        # Extract fields based on the bitfield mask - CORRECTED to use left-to-right numbering
        fields = []
        for byte_idx, byte in enumerate(bitfield):
            for bit_idx in range(8):
                # Bit 7 is leftmost (MSB) and corresponds to field 1 + (byte_idx * 8)
                # Bit 0 is rightmost (LSB) and corresponds to field 8 + (byte_idx * 8)
                field_num = 1 + (byte_idx * 8) + (7 - bit_idx)
                if byte & (1 << bit_idx):
                    fields.append(field_num)

        # Prepare result
        result = {
            "ntcb_header": binascii.hexlify(ntcb_header).decode('ascii'),
            "flex_marker": flex_marker,
            "direction": direction,
            "protocol": "FLEX",
            "protocol_version": protocol_version,
            "struct_version": struct_version,
            "data_size": data_size,
            "mask_size": self.struct_sizes.get(struct_version, 0),
            "fields": fields
        }

        return result

    def format_output(self, parse_result):
        if "error" in parse_result:
            return f"Error: {parse_result['error']}"

        fields_str = " ".join([f"#{n}" for n in sorted(parse_result["fields"])])

        output = f"Разбор сообщения {parse_result['flex_marker']}\n"
        output += f" Определена маска FLEX со следующими полями: {fields_str}\n"
        output += f" cmd={parse_result['flex_marker']}; proto={parse_result['protocol']}; "
        output += f"ver={parse_result['protocol_version']}; struct={parse_result['struct_version']}; "
        output += f"mask_size={parse_result['mask_size']};"

        return output


def parse_flex_message(hex_string):
    parser = FlexProtocolParser()
    result = parser.parse_hex(hex_string)
    return parser.format_output(result)


# Example usage
if __name__ == "__main__":
    # Example from the documentation
    hex_string = "404E544301000000000000002A0096A42A3E464C4558B01E1EFFF3FE300A08000F83AA00000000280008002300000000000000C0000000000000"
    print(parse_flex_message(hex_string))
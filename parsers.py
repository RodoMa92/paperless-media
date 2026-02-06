from pathlib import Path
import random
import os

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont, ImageOps

from documents.parsers import DocumentParser, ParseError

class MediaDocumentParser(DocumentParser):
    logging_name = "paperless.parsing.media"

    def get_random_color(self):
        """Generate a random, visually pleasing background color"""
        # Using pastel colors for better readability
        return (
            random.randint(100, 200),  # R
            random.randint(100, 200),  # G
            random.randint(100, 200),  # B
        )

    def get_text_color(self, background_color):
        """Determine if text should be black or white based on background brightness"""
        brightness = sum(background_color) / 3
        return "black" if brightness > 150 else "white"

    def get_thumbnail(self, document_path: Path, mime_type: str, file_name=None) -> Path:
        # Check if the file is a video based on MIME type
        if mime_type.startswith("video/"):
            video_thumbnail = self.get_video_thumbnail(document_path, mime_type, file_name)
            if video_thumbnail:
                return video_thumbnail
        elif mime_type == "application/dicom":
            image_thumbnail = self.get_medical_thumbnail(document_path, mime_type, file_name)
            if image_thumbnail:
                return image_thumbnail
        
        return self.get_dynamic_thumbnail(document_path, mime_type, file_name)


    def get_dynamic_thumbnail(self, document_path: Path, mime_type, file_name=None) -> Path:
        # For non-video files or fallback, generate a default icon
        # Get file extension or mime type subtype
        if file_name:
            ext = os.path.splitext(file_name)[1].upper()
            if not ext:
                ext = mime_type.split('/')[-1].upper()
        else:
            ext = mime_type.split('/')[-1].upper()

        # Remove the dot if it exists
        ext = ext.lstrip('.')

        # Create a square thumbnail
        size = (400, 400)
        bg_color = self.get_random_color()
        img = Image.new("RGB", size, color=bg_color)
        draw = ImageDraw.Draw(img)

        # Calculate font size (dynamic based on text length)
        font_size = min(size[0] // (len(ext) + 2), size[1] // 3)
        font = ImageFont.truetype(
            font=settings.THUMBNAIL_FONT_NAME,
            size=font_size,
            layout_engine=ImageFont.Layout.BASIC,
        )

        # Get text size for centering
        text_bbox = draw.textbbox((0, 0), ext, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        # Calculate center position
        x = (size[0] - text_width) // 2
        y = (size[1] - text_height) // 2

        # Draw text
        text_color = self.get_text_color(bg_color)
        draw.text((x, y), ext, font=font, fill=text_color)

        # Save as WebP
        out_path = self.tempdir / "thumb.webp"
        img.save(out_path, format="WEBP")

        return out_path

    def get_video_thumbnail(self, video_path: Path, mime_type, file_name=None) -> Path:
        """Extract a frame from a video file to use as a thumbnail, if moviepy is available."""
        try:
            from moviepy import VideoFileClip
        except ImportError:
            self.logger.warning("moviepy is not installed. Falling back to default icon generation.")
            return self.get_dynamic_thumbnail(video_path, mime_type, file_name)

        try:
            # Load the video file
            clip = VideoFileClip(str(video_path))

            # Extract a frame at 30 seconds (or the middle if shorter)
            frame_time = min(30, clip.duration / 2)
            frame = clip.get_frame(frame_time)

            # Convert the frame to an image
            img = Image.fromarray(frame)

            # Resize to thumbnail size
            img.thumbnail((400, 400))

            # Save as WebP
            out_path = self.tempdir / "thumb.webp"
            img.save(out_path, format="WEBP")

            return out_path
        except Exception as e:
            self.logger.warning(f"Failed to extract video thumbnail: {e}")
            return self.get_dynamic_thumbnail(video_path, mime_type, file_name)

    def get_medical_thumbnail(self, image_path: Path, mime_type, file_name=None):
        try:
            from pydicom import dcmread
        except ImportError:
            self.logger.warning("pydicom is not installed. Falling back to default icon generation.")
            return self.get_dynamic_thumbnail(image_path, mime_type, file_name)
        try:
            # Read the image
            data = dcmread(image_path)

            # Get a numpy array out...
            numpy_array = data.pixel_array

            # Convert the grayscale image back to a Python Image Library format...
            pil_image = Image.fromarray(numpy_array)
            
            # Fix the contrast for a proper visualization...
            pil_image = ImageOps.autocontrast(pil_image, 0.1)

            # Resize to thumbnail size...
            pil_image.thumbnail((400, 400))

            # finally save as WebP
            out_path = self.tempdir / "thumb.webp"
            pil_image.save(out_path, format="WEBP")
            return out_path
        except Exception as e:
            self.logger.warning(f"Failed to extract medical image thumbnail: {e}")
            return self.get_dynamic_thumbnail(image_path, mime_type, file_name)

    def parseDICOMTime(self, time_str: str):
        from datetime import datetime
        import re
        try:
            match (len(time_str)):
                case 2:
                    try:
                        return datetime.strptime(time_str, "%H").strftime("%H:00:00")
                    except ValueError as e:
                        self.logger.warning(f"Invalid hour (00-23): {e}")
                        return "Unknown"
                case 4:
                    try:
                        return datetime.strptime(time_str, "%H%M").strftime("%H:%M:00")
                    except ValueError as e:
                        self.logger.warning(f"Invalid hour (00-23) or minutes (00-59): {e}")
                        return "Unknown"
                case 6:
                    try:
                        return datetime.strptime(time_str, "%H%M%S").strftime("%H:%M:%S")
                    except ValueError as e:
                        self.logger.warning(f"Invalid hour (00-23),minutes (00-59) or seconds (00-59): {e}")
                        return "Unknown"
                case n if n in range(8,13+1):
                    matches = re.match(r"(\d{6})\.(\d{1,6})", time_str)
                    if matches is not None:
                        try:
                            return datetime.strptime(matches[1] + matches[2].ljust(6,"0"), "%H%M%S%f").strftime("%H:%M:%S.%f")
                        except ValueError as e:
                            self.logger.warning(f"Invalid millisecond format: {e}")
                            return "Unknown"
                    else:
                        self.logger.warning("Invalid millisecond format")
                        return "Unknown"
                case _:
                        self.logger.warning("Invalid time field length")
                        return "Unknown"
        except ValueError:
            return "Unknown"

    def parseDICOMDate(self, date_str: str):
        from datetime import datetime
        try:
            return datetime.strptime(date_str, "%Y%m%d").strftime("%d/%m/%Y")
        except ValueError as e:
            self.logger.warning(f"Failed to parse date field: {e}")
            return "Unknown"
    
    def parseDICOMAge(self, age_str: str):
        import re
        extracted = re.match(r"(\d{3})([DWMY])", age_str)
        if extracted is not None:
            value = str(extracted[1])
            match (extracted[2]):
                case 'D':
                    return f"{int(value)} day(s)"
                case 'W':
                    return f"{int(value)} week(s)"
                case 'M':
                    return f"{int(value)} month(s)"
                case 'Y':
                    return f"{int(value)} year(s)"
                case _:
                    raise Exception("I should never end up in this case!")
        return "Date format invalid"

    def parseDICOMName(self, name_str: str):
        import re
        extracted = re.split("^", name_str)
        if len(extracted) > 5:
            return "Invalid name"
        elif len(extracted) is 1:
            return extracted[0]
        # Get the first and last name and swap them
        ln = extracted.pop(0)
        fn = extracted.pop(0)
        parsed = fn + " " + ln + " "
        # ... then parse the rest of the additional ones and append it to the result.
        for name in extracted:
            parsed += name + " "
        return parsed


    def parseDICOMDocument(self, file_path: Path):
        try:
            from pydicom import dcmread
            from pydicom.errors import InvalidDicomError
        except ImportError:
            self.logger.warning("pydicom is not installed. Unable to parse image metadata, returning empty document.")
            return ""
        try:
            data = dcmread(file_path)
            return f"Type of DICOM data: {data.SOPClassUID.name if "SOPClassUID" in data else "Unknown"},\n\
                    Patient Name: {self.parseDICOMName(data.PatientName) if "PatientName" in data else "Unknown"},\n\
                    Birth Date: {self.parseDICOMDate(data.PatientBirthDate) if "PatientBirthDate" in data else "Unknown"},\n\
                    Patient Age: {self.parseDICOMAge(data.PatientAge) if "PatientAge" in data else "Unknown"},\n\
                    Patient Sex: {("Male" if data.PatientSex is "M" else "Female") if "PatientSex" in data else "Unknown"},\n\
                    Institution Name: {data.InstitutionName if "InstitutionName" in data else "Unknown"},\n\
                    Acquisition Date: {self.parseDICOMDate(data.AcquisitionDate) if "AcquisitionDate" in data else "Unknown"},\n\
                    Acquisition Time: {self.parseDICOMTime(data.AcquisitionTime) if "AcquisitionTime" in data else "Unknown"},\n\
                    "
        except InvalidDicomError as e:
            self.logger.error(f"Invalid DICOM document. {e}")
            # Invalid document, stop processing it and reject it.
            raise ParseError
        except Exception as e:
            self.logger.warning(f"Failed to extract text from DICOM image: {e}")
            return ""


    def parse(self, document_path: Path, mime_type: str, file_name=None):
        import re

        def is_meaningful_text(text):
            # Remove non-printable characters
            text = ''.join(c for c in text if c.isprintable())

            # Check if the text contains at least 5 recognizable words
            words = re.findall(r'\b\w+\b', text)
            return len(words) >= 5

        try:
            # Handle specific mime types
            if mime_type.startswith("audio/") or mime_type.startswith("video/") or mime_type == "application/octet-stream":
                self.text = ""
                return
            
            # Pull out the main fields from the dicom image data
            if mime_type is "application/dicom":
                self.text = self.parseDICOMDocument(document_path)
                return


            # Attempt to read only the first 5 KB of the file
            with open(document_path, "rb") as file:
                raw_data = file.read(5000)

            # Try decoding the raw data to text
            raw_text = raw_data.decode("utf-8", errors="ignore")

            # Remove null bytes from the extracted text
            sanitized_text = raw_text.replace("\x00", "")

            # Keep only standard characters (A-Z, a-z, 0-9, and standard special characters)
            sanitized_text = re.sub(r"[^A-Za-z0-9!@#$%^&*()_+\-=\[\]{}\\|;:'\",<.>/?`~\\s]", "", sanitized_text)


            if mime_type.startswith("text/"):
                # Use the full text for text/* mime types
                self.text = sanitized_text
            else:
                # Validate the text for meaningful content for other mime types
                if is_meaningful_text(sanitized_text):
                    self.text = sanitized_text
                else:
                    self.text = ""
        except Exception as e:
            # Log the exception and set a fallback message for unsupported file types
            self.logger.warning(f"Error parsing file {file_name or document_path}: {e}")
            self.text = ""

    def get_settings(self):
        """
        This parser does not implement additional settings yet
        """
        return None
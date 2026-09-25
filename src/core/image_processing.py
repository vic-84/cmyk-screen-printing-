# Origen: claude_test_fixed_lpi.py
# Sección: Funciones de procesamiento de imágenes

import cv2
import numpy as np


def prepare_image_for_processing(img):
    """Convierte imágenes con transparencia a BGR sobre fondo blanco."""
    if img is None:
        raise ValueError("La imagen no puede ser nula")

    if img.ndim != 3 or img.shape[2] != 4:
        return img

    alpha = img[:, :, 3:4].astype(np.float32) / 255.0
    bgr = img[:, :, :3].astype(np.float32)
    return np.clip(bgr * alpha + 255 * (1 - alpha), 0, 255).astype(np.uint8)

def enhance_resolution(img, scale=2.0):
    """Aumenta la resolución de una imagen con interpolación bicúbica"""
    try:
        h, w = img.shape[:2]
        new_w, new_h = int(w * scale), int(h * scale)
        enhanced = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        print(f"✅ Resolución mejorada: {w}x{h} → {new_w}x{new_h}")
        return enhanced
    except Exception as e:
        print(f"⚠️ Error mejorando resolución: {e}")
        return img.copy()

def enhance_image_resolution(img, factor=2.0, method='INTER_CUBIC'):
    """Mejorar resolución de imagen usando interpolación avanzada"""
    if factor == 1.0 or method is None:
        return img.copy()
    
    print(f"🔍 Mejorando resolución {factor}x usando {method}")
    
    # Mapear métodos de interpolación
    interpolation_methods = {
        'INTER_CUBIC': cv2.INTER_CUBIC,
        'INTER_LANCZOS4': cv2.INTER_LANCZOS4,
        'INTER_LINEAR': cv2.INTER_LINEAR
    }
    
    try:
        h, w = img.shape[:2]
        new_w = int(w * factor)
        new_h = int(h * factor)
        
        # Aplicar mejora de resolución
        enhanced = cv2.resize(
            img, 
            (new_w, new_h), 
            interpolation=interpolation_methods.get(method, cv2.INTER_CUBIC)
        )
        
        print(f"✅ Resolución mejorada: {w}x{h} → {new_w}x{new_h}")
        return enhanced
        
    except Exception as e:
        print(f"⚠️ Error mejorando resolución: {e}")
        return img.copy()

def generate_white_base(img, threshold=240, choke=2):
    """Generar máscara de base blanca para serigrafía en playeras oscuras"""
    print("⚪ Generando base blanca automática...")
    
    try:
        # Convertir a escala de grises si es necesario
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img.copy()
        
        # Detectar áreas que necesitan base blanca (áreas claras)
        # Invertir: áreas oscuras en la imagen original serán blancas en la base
        white_base_mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)[1]
        
        # Choke: la base se contrae para que no asome por los bordes del color
        # cuando el registro de prensa no es perfecto
        if choke > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (choke*2+1, choke*2+1))
            white_base_mask = cv2.morphologyEx(white_base_mask, cv2.MORPH_ERODE, kernel)
        
        # Suavizar bordes
        white_base_mask = cv2.GaussianBlur(white_base_mask, (3, 3), 0)
        
        print("✅ Máscara de base blanca generada")
        return white_base_mask
        
    except Exception as e:
        print(f"⚠️ Error generando base blanca: {e}")
        # Retornar máscara vacía en caso de error
        return np.zeros(img.shape[:2], dtype=np.uint8)

def detect_image_complexity(img):
    """Detectar complejidad de imagen para recomendar resolución"""
    try:
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img.copy()
        
        # Calcular gradientes para detectar detalles
        grad_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        gradient_magnitude = np.sqrt(grad_x**2 + grad_y**2)
        
        # Métricas de complejidad
        detail_density = np.mean(gradient_magnitude)
        edge_pixels = np.sum(gradient_magnitude > 30)
        total_pixels = gray.shape[0] * gray.shape[1]
        edge_ratio = edge_pixels / total_pixels
        
        # Clasificar complejidad
        if detail_density > 15 and edge_ratio > 0.1:
            return "alta", "Se recomienda mejorar resolución 200-300%"
        elif detail_density > 8 and edge_ratio > 0.05:
            return "media", "Se recomienda mejorar resolución 150-200%"
        else:
            return "baja", "Resolución original puede ser suficiente"
            
    except Exception as e:
        print(f"⚠️ Error analizando complejidad: {e}")
        return "desconocida", "Recomendación no disponible"

def rotate_image(img, angle):
    """Rotar imagen manteniendo todo el contenido visible - versión mejorada"""
    if angle == 0:
        return img.copy()
    
    try:
        h, w = img.shape[:2]
        center = (w // 2, h // 2)
        
        # Calcular matriz de rotación
        rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        
        # Calcular nuevas dimensiones para contener toda la imagen rotada
        cos_angle = abs(rotation_matrix[0, 0])
        sin_angle = abs(rotation_matrix[0, 1])
        new_w = int((h * sin_angle) + (w * cos_angle))
        new_h = int((h * cos_angle) + (w * sin_angle))
        
        # Ajustar matriz de traducción
        rotation_matrix[0, 2] += (new_w / 2) - center[0]
        rotation_matrix[1, 2] += (new_h / 2) - center[1]
        
        # Aplicar rotación con fondo blanco
        rotated = cv2.warpAffine(
            img, rotation_matrix, (new_w, new_h), 
            borderMode=cv2.BORDER_CONSTANT, 
            borderValue=255
        )
        
        # Asegurar que sea continuo en memoria y del tipo correcto
        return np.ascontiguousarray(rotated, dtype=np.uint8)
        
    except Exception as e:
        print(f"⚠️ Error en rotación: {e}")
        return img.copy()

def resize_to_print_format(img, print_format, target_dpi):
    """Redimensionar imagen para ajustar al formato de impresión"""
    try:
        # Obtener dimensiones del formato en píxeles
        width_mm = print_format["width"]
        height_mm = print_format["height"]
        
        # Convertir a píxeles según DPI
        width_px = int((width_mm / 25.4) * target_dpi)
        height_px = int((height_mm / 25.4) * target_dpi)
        
        # Obtener dimensiones actuales
        if len(img.shape) == 3:
            current_h, current_w, _ = img.shape
        else:
            current_h, current_w = img.shape
        
        # Calcular escala manteniendo proporción
        scale_x = width_px / current_w
        scale_y = height_px / current_h
        scale = min(scale_x, scale_y)
        
        # Nuevas dimensiones
        new_w = int(current_w * scale)
        new_h = int(current_h * scale)
        
        # Redimensionar imagen
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        
        # Crear imagen del tamaño del formato con fondo blanco
        if len(img.shape) == 3:
            final_img = np.ones((height_px, width_px, 3), dtype=np.uint8) * 255
        else:
            final_img = np.ones((height_px, width_px), dtype=np.uint8) * 255
        
        # Centrar imagen redimensionada
        start_x = (width_px - new_w) // 2
        start_y = (height_px - new_h) // 2
        
        if len(img.shape) == 3:
            final_img[start_y:start_y+new_h, start_x:start_x+new_w] = resized
        else:
            final_img[start_y:start_y+new_h, start_x:start_x+new_w] = resized
        
        print(f"✅ Imagen ajustada a formato {print_format['name']}: {width_px}×{height_px} px @ {target_dpi} DPI")
        return final_img
        
    except Exception as e:
        print(f"⚠️ Error ajustando al formato: {e}")
        return img.copy()

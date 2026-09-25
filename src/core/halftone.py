# Origen: claude_test_fixed_lpi.py
# Sección: Funciones de procesamiento de halftone

import cv2
import numpy as np
from .image_processing import rotate_image

def apply_floyd_steinberg_halftone(img):
    """Halftone usando dithering Floyd-Steinberg (blanco y negro)"""
    img = img.astype(np.float32)
    out = np.copy(img)
    h, w = img.shape
    for y in range(h):
        for x in range(w):
            old_pixel = out[y, x]
            new_pixel = 0 if old_pixel < 128 else 255
            out[y, x] = new_pixel
            quant_error = old_pixel - new_pixel
            if x + 1 < w:
                out[y, x + 1] += quant_error * 7 / 16
            if y + 1 < h:
                if x > 0:
                    out[y + 1, x - 1] += quant_error * 3 / 16
                out[y + 1, x] += quant_error * 5 / 16
                if x + 1 < w:
                    out[y + 1, x + 1] += quant_error * 1 / 16
    return np.clip(out, 0, 255).astype(np.uint8)

def apply_simple_halftone(img, scale=10, shape='circle'):
    """Función mejorada de halftone con formas adicionales (sin ángulo)"""
    h, w = img.shape
    out = np.ones((h, w), dtype=np.uint8) * 255
    
    for y in range(0, h, scale):
        for x in range(0, w, scale):
            roi = img[y:y+scale, x:x+scale]
            if roi.size == 0:
                continue
                
            val = int(np.mean(roi))
            intensity = val / 255.0
            size = max(1, int(intensity * (scale // 2)))
            center = (x + scale // 2, y + scale // 2)
            
            if shape == 'circle':
                cv2.circle(out, center, size, 0, -1)
            elif shape == 'ellipse':
                # Elipse horizontal
                axes = (size, max(1, size // 2))
                cv2.ellipse(out, center, axes, 0, 0, 360, 0, -1)
            elif shape == 'diamond':
                # Crear forma de diamante
                points = np.array([
                    [center[0], center[1] - size],      # arriba
                    [center[0] + size, center[1]],      # derecha
                    [center[0], center[1] + size],      # abajo
                    [center[0] - size, center[1]]       # izquierda
                ], np.int32)
                cv2.fillPoly(out, [points], 0)
            elif shape == 'line':
                # Líneas paralelas (efecto lineal)
                thickness = max(1, size // 2)
                # Líneas horizontales
                for i in range(0, scale, max(2, scale//4)):
                    if y + i < h:
                        line_opacity = int(intensity * thickness)
                        if line_opacity > 0:
                            cv2.line(out, (x, y + i), (x + scale, y + i), 
                                   255 - int(intensity * 255), line_opacity)
    
    return out

def apply_cmyk_halftone(img, scale=10, shape='circle', angle=0, channel_name='C'):
    """Función de halftone con ángulos de cuatricromía profesional - versión mejorada"""
    print(f"🎯 Aplicando halftone al canal {channel_name} con ángulo {angle}°")
    
    # Verificar imagen de entrada
    if img is None or img.size == 0:
        print(f"❌ Error: Imagen vacía para canal {channel_name}")
        return np.ones((100, 100), dtype=np.uint8) * 255
    
    h, w = img.shape
    
    # Si no hay rotación, usar función simple optimizada
    if angle == 0:
        result = apply_simple_halftone(img, scale, shape)
        return np.ascontiguousarray(result, dtype=np.uint8)
    
    # Para ángulos, usar aproximación más estable
    try:
        # Método simplificado: rotar imagen, aplicar halftone, rotar de vuelta
        # Esto es más estable que rotar la grilla
        
        # 1. Rotar imagen original
        rotated_img = rotate_image(img, -angle)  # Rotar en sentido contrario
        
        # 2. Aplicar halftone a imagen rotada
        halftoned_rotated = apply_simple_halftone(rotated_img, scale, shape)
        
        # 3. Rotar resultado de vuelta
        final_result = rotate_image(halftoned_rotated, angle)
        
        # 4. Recortar al tamaño original si es necesario
        result_h, result_w = final_result.shape
        if result_h != h or result_w != w:
            # Centrar y recortar
            start_y = max(0, (result_h - h) // 2)
            start_x = max(0, (result_w - w) // 2)
            end_y = min(result_h, start_y + h)
            end_x = min(result_w, start_x + w)
            
            # Crear imagen de salida del tamaño correcto
            final_output = np.ones((h, w), dtype=np.uint8) * 255
            
            # Calcular área a copiar
            copy_h = min(h, end_y - start_y)
            copy_w = min(w, end_x - start_x)
            
            if copy_h > 0 and copy_w > 0:
                final_output[:copy_h, :copy_w] = final_result[start_y:start_y+copy_h, start_x:start_x+copy_w]
            
            final_result = final_output
        
        print(f"✅ Halftone con ángulo {angle}° aplicado al canal {channel_name}")
        return np.ascontiguousarray(final_result, dtype=np.uint8)
        
    except Exception as e:
        print(f"⚠️ Error en halftone con ángulo para {channel_name}: {e}")
        print(f"   Usando halftone sin ángulo como respaldo")
        result = apply_simple_halftone(img, scale, shape)
        return np.ascontiguousarray(result, dtype=np.uint8)
import os
import aspose.cad as cad

def batch_convert_cad_to_pdf(cad_in_folder, pdf_out_folder):
    if not os.path.exists(pdf_out_folder):
        os.makedirs(pdf_out_folder, exist_ok=True)
        
    valid_exts = (".dwg", ".dxf")

    for filename in os.listdir(cad_in_folder):
        if filename.lower().endswith(valid_exts):
            in_path = os.path.join(cad_in_folder, filename)

            out_file = os.path.splitext(filename)[0] + '.pdf'
            out_path = os.path.join(pdf_out_folder, out_file)

            print(f'Processing: {filename}...')

            try:
                #load file
                image = cad.Image.load(in_path)

                #set highres options
                raster_options = cad.imageoptions.CadRasterizationOptions()
                raster_options.layers = ['*']
                raster_options.page_width = 800.0
                raster_options.page_height = 600.0
                raster_options.automatic_layouts_scaling = True

                #set pdf options
                pdf_options = cad.imageoptions.PdfOptions()
                pdf_options.vector_rasterization_options = raster_options

                #export
                image.save(out_path, pdf_options)
                print(f"Processing Done! File saved to: {out_path}")
            except Exception as e:
                print(f"...Error converting {filename}: {e}")

batch_convert_cad_to_pdf("cads/", "exported_pdfs/")

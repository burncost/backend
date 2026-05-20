import os
import aspose.cad as cad

def batch_convert_cad_to_pdf(cad_in_folder, pdf_out_folder):
    if not os.path.exists(pdf_out_folder):
        os.makedir(pdf_out_folder)
        
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
                
##import ezdxf
##from ezdxf import recover
##from ezdxf.addons.drawing import RenderContext, Frontend
##from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
##import matplotlib.pyplot as plt
##
##def convert_cad_to_pdf(cad_file_in, pdf_file_out):
##    try:
##        doc, aud = recover.readfile(cad_file_in)
##
##        if aud.has_errors:
##            print(f"Found errors in file: {len(aud.errors)}")
##
##        fig = plt.figurre(dpi=300)
##        ax = fig.add_axes([0,0,1,1])
##        ctx = RenderContext(doc)
##        out = MatplotlibBackend(ax)
##
##        Frontend(ctx, out).draw_layout(doc.modelspace(), finalize=True)
##
##        #save to pdf
##        fig.savefig(pdf_file_out, format-'pdf', bbox_inches='tight')
##        plt.close(fig)
##        print(f"Success! PDF File Created and Saved to {pdf_file_out}")
##    except Exception as e:
##        print(f"Error: {e}")
##
##convert_cad_to_pdf("test_cad","test_cad.pdf")

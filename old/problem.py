from PIL import Image, ImageDraw


def create_circle_image(image_size, circle_center, circle_radius, output_path=None):
    """
    创建一个黑白图片并在指定位置绘制白色实心圆

    参数:
        image_size: 图片尺寸，元组形式 (width, height)
        circle_center: 圆心坐标，元组形式 (x, y)
        circle_radius: 圆的半径（像素）
        output_path: 图片保存路径（可选），如果不提供则只返回图像对象

    返回:
        PIL Image对象
    """
    # 创建黑色背景图像（'L'模式表示8位黑白图像）
    img = Image.new('L', image_size, color=0)
    draw = ImageDraw.Draw(img)

    # 计算圆的边界框
    x, y = circle_center
    r = circle_radius
    bounding_box = (x - r, y - r, x + r, y + r)

    # 绘制白色实心圆（255表示白色）
    draw.ellipse(bounding_box, fill=255)

    if output_path:
        img.save(output_path)

    return img


# 示例用法
if __name__ == "__main__":
    # 创建一个500x300像素的图片
    img_size = (1440, 2560)

    # 圆心在(200, 150)，半径100像素
    center = (519, 1479)
    radius = 400

    # 创建并保存图片
    image = create_circle_image(img_size, center, radius, "circle_image.png")

    # 显示图片
    image.show()